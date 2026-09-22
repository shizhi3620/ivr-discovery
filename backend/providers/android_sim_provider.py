"""Android SIM gateway provider (China mainland path).

Places real cellular calls through a rooted Android phone running
`s-xander/Android-sip-gateway`, registered to a local FreeSWITCH. The provider
talks to FreeSWITCH over ESL, originates a call to the registered gateway
extension, and passes the GSM destination in the `X-GSM-Destination` SIP header
(see gateway/freeswitch/README.md).

The SIM gateway carries audio; Tencent Cloud provides the ASR/TTS stage behind
the Audio Provider boundary. If Tencent credentials are absent, this provider
advertises no transcript/speech capability and refuses to dial, rather than
wasting a real cellular call.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import uuid as uuid_lib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import websockets

from audio import AudioProvider, AudioProviderError, get_audio_provider
from providers.base import (
    CallResult,
    ProviderCapabilities,
    ProviderCapabilityError,
    TranscriptCallback,
    STATUS_COMPLETED,
    STATUS_ERROR,
    STATUS_IN_PROGRESS,
    STATUS_UNKNOWN,
)
from providers.esl import EslClient, EslConfig, EslError

logger = logging.getLogger(__name__)

# How long each DTMF key is held before release, mirroring the verified smoke test.
DTMF_TONE_MS = 250


@dataclass
class GatewayConfig:
    """Where the Android gateway and its FreeSWITCH live."""

    esl_host: str = "127.0.0.1"
    esl_port: int = 8021
    esl_password: str = ""
    domain: str = ""
    gateway_extension: str = "gateway1"
    caller_extension: str = "softphone"
    # Wait this long after the call is answered before the first DTMF key, so the
    # IVR greeting has time to finish.
    dtmf_initial_delay: float = 8.0
    # Wait this long between compound-path keys ("1w3").
    dtmf_key_gap: float = 2.0
    realtime_relay_url: str = "ws://127.0.0.1:18031"
    realtime_dtmf_enabled: bool = True
    # FreeSWITCH writes one WAV per call into this directory.
    recording_dir: str = "/tmp/ivr-discovery-recordings"
    # Wait briefly for bgapi originate to create a visible channel.
    channel_lookup_timeout: float = 10.0
    # Wait this long for the gateway leg to accept uuid_record.
    recording_start_timeout: float = 30.0
    # Wait briefly for FreeSWITCH to finish flushing the WAV after hangup.
    recording_finalize_timeout: float = 5.0

    @classmethod
    def from_env(cls, env: dict | None = None) -> "GatewayConfig":
        source = env if env is not None else os.environ
        if env is None:
            source = _merge_local_gateway_env(source)
        esl = EslConfig.from_env(source)
        return cls(
            esl_host=esl.host,
            esl_port=esl.port,
            esl_password=esl.password,
            domain=source.get("FREESWITCH_DOMAIN", source.get("FREESWITCH_ESL_HOST", "127.0.0.1")),
            gateway_extension=source.get("SIP_GATEWAY_EXTENSION", "gateway1"),
            caller_extension=source.get("SIP_SOFTPHONE_EXTENSION", "softphone"),
            dtmf_initial_delay=float(
                source.get("CALL_DTMF_INITIAL_DELAY", "8")
            ),
            dtmf_key_gap=float(source.get("CALL_DTMF_KEY_GAP", "2")),
            realtime_relay_url=source.get(
                "REALTIME_RELAY_URL",
                "ws://127.0.0.1:18031",
            ),
            realtime_dtmf_enabled=source.get(
                "REALTIME_DTMF_ENABLED",
                "1",
            ).lower()
            in ("1", "true", "yes"),
            recording_dir=source.get("CALL_RECORDING_DIR", "/tmp/ivr-discovery-recordings"),
            channel_lookup_timeout=float(
                source.get("CALL_CHANNEL_LOOKUP_TIMEOUT", "10")
            ),
            recording_start_timeout=float(
                source.get("CALL_RECORDING_START_TIMEOUT", "30")
            ),
            recording_finalize_timeout=float(
                source.get("CALL_RECORDING_FINALIZE_TIMEOUT", "5")
            ),
        )


def _merge_local_gateway_env(source: Mapping[str, str]) -> dict[str, str]:
    """Reuse the local FreeSWITCH harness config without duplicating secrets.

    Production still supplies normal environment variables. The fallback only
    applies when the backend and gateway are checked out together.
    """
    gateway_env = (
        Path(__file__).resolve().parents[2]
        / "gateway"
        / "freeswitch"
        / ".env.local"
    )
    if not gateway_env.is_file():
        return dict(source)

    from dotenv import dotenv_values

    local = dotenv_values(gateway_env)
    merged = dict(source)
    aliases = {
        "FREESWITCH_ESL_PASSWORD": "ESL_PASSWORD",
        "FREESWITCH_ESL_PORT": "ESL_PORT",
        "FREESWITCH_DOMAIN": "LAN_IP",
    }
    for target, alias in aliases.items():
        if not merged.get(target) and local.get(alias):
            merged[target] = str(local[alias])
    return merged


class AndroidSimGatewayProvider:
    name = "android_sim"
    capabilities = ProviderCapabilities(
        transcript=False,
        speech=False,
        dtmf=True,
        realtime_dtmf=True,
    )

    def __init__(
        self,
        config: GatewayConfig | None = None,
        audio_provider: AudioProvider | None = None,
    ):
        self.config = config or GatewayConfig.from_env()
        self.audio_provider = audio_provider or get_audio_provider()
        audio_configured = bool(
            self.audio_provider
            and getattr(self.audio_provider, "is_configured", False)
        )
        self.capabilities = ProviderCapabilities(
            transcript=audio_configured,
            speech=audio_configured,
            dtmf=True,
            realtime_dtmf=self.config.realtime_dtmf_enabled,
        )
        self._esl_config = EslConfig(
            host=self.config.esl_host,
            port=self.config.esl_port,
            password=self.config.esl_password,
        )
        self._transcripts: dict[str, str] = {}
        self._realtime_results: dict[str, dict] = {}
        self._realtime_tasks: dict[str, asyncio.Task] = {}

    # -- provider interface ---------------------------------------------------

    async def place_call(
        self,
        phone_number: str,
        *,
        task: str | None = None,
        dtmf_sequence: str | None = None,
        voice_option: str | None = None,
        max_duration: int = 60,
    ) -> str:
        self._require_audio_capability()
        if task is not None:
            raise ProviderCapabilityError(
                "Android SIM gateway does not run a general on-call speech agent; "
                "use dtmf_sequence or voice_option"
            )

        correlation_id = str(uuid_lib.uuid4())
        dest = phone_number.strip()
        if not dest:
            raise ValueError("phone_number is required")

        recording_dir = Path(self.config.recording_dir)
        await asyncio.to_thread(recording_dir.mkdir, parents=True, exist_ok=True)

        voice_prompt_path: Path | None = None
        if voice_option:
            if not self.capabilities.speech:
                raise ProviderCapabilityError(
                    "Android SIM gateway cannot speak voice options because "
                    "Tencent TTS is not configured"
                )
            voice_prompt_path = recording_dir / f"{correlation_id}-prompt.wav"
            await self.audio_provider.synthesize(voice_option, voice_prompt_path)
            self._validate_recording_path(voice_prompt_path)

        originate = (
            "originate "
            "{"
            f"ivr_discovery_call_id={correlation_id},"
            f"origination_caller_id_number={correlation_id},"
            "ignore_early_media=true,"
            "RECORD_STEREO=true,"
            f"sip_h_X-GSM-Destination={dest}"
            f"}}user/{self.config.gateway_extension}@{self.config.domain} "
            "&park()"
        )

        await asyncio.to_thread(self._run_api, f"bgapi {originate}")
        call_id = await self._find_channel(correlation_id)
        recording_path = self._recording_path(call_id)
        self._validate_recording_path(recording_path)
        try:
            await self._start_recording(call_id, recording_path)
        except EslError:
            await self.stop_call(call_id)
            raise

        # Enforce max_duration: schedule a hangup so a parked call cannot live forever.
        schedule = f"sched_api +{int(max_duration)} {call_id} uuid_kill {call_id}"
        try:
            await asyncio.to_thread(self._run_api, schedule)
        except EslError:
            logger.warning("Could not schedule max-duration hangup for %s", call_id)

        if dtmf_sequence:
            if not self.capabilities.realtime_dtmf:
                await self.stop_call(call_id)
                raise ProviderCapabilityError(
                    "Realtime DTMF navigation is disabled; refusing timed branch navigation"
                )
            self._realtime_tasks[call_id] = asyncio.create_task(
                self._navigate_realtime(call_id, dtmf_sequence)
            )
        if voice_prompt_path is not None:
            asyncio.create_task(
                self._play_voice_after_answer(call_id, voice_prompt_path)
            )

        logger.info("Originated GSM call %s -> %s", call_id, dest)
        return call_id

    async def wait_for_call(
        self,
        call_id: str,
        on_transcript: TranscriptCallback | None = None,
    ) -> CallResult:
        result = await self.get_call(call_id)
        while result.status == STATUS_IN_PROGRESS:
            await asyncio.sleep(2.0)
            result = await self.get_call(call_id)

        realtime_task = self._realtime_tasks.get(call_id)
        if realtime_task is not None and not realtime_task.done():
            await realtime_task

        # The channel disappears slightly before FreeSWITCH finishes flushing
        # the WAV, so explicitly finalize once the call is terminal.
        if result.status == STATUS_COMPLETED and not result.transcript:
            result = await self._finalize_recording(call_id)

        if on_transcript and result.transcript:
            callback_result = on_transcript(result.transcript)
            if inspect.isawaitable(callback_result):
                await callback_result
        realtime = self._realtime_results.get(call_id)
        if realtime:
            result.realtime_fault = str(realtime.get("fault") or "")
        return result

    async def get_call(self, call_id: str) -> CallResult:
        try:
            channels = await asyncio.to_thread(self._run_api, "show channels as json")
        except EslError as exc:
            logger.warning("ESL query failed for %s: %s", call_id, exc)
            return CallResult(call_id=call_id, status=STATUS_ERROR, capabilities=self.capabilities)

        active = self._channel_active(channels, call_id)
        if active:
            return CallResult(
                call_id=call_id,
                status=STATUS_IN_PROGRESS,
                capabilities=self.capabilities,
            )

        if call_id in self._transcripts:
            return CallResult(
                call_id=call_id,
                status=STATUS_COMPLETED,
                transcript=self._transcripts[call_id],
                capabilities=self.capabilities,
            )

        recording_path = self._recording_path(call_id)
        if recording_path.is_file():
            return await self._transcribe_recording(call_id, recording_path)

        return CallResult(
            call_id=call_id,
            status=STATUS_COMPLETED,
            transcript="",
            cost=0.0,
            capabilities=self.capabilities,
        )

    async def stop_call(self, call_id: str) -> None:
        try:
            await asyncio.to_thread(self._run_api, f"uuid_kill {call_id}")
        except EslError as exc:
            logger.warning("Failed to stop call %s: %s", call_id, exc)

    # -- helpers --------------------------------------------------------------

    async def _navigate_realtime(self, call_id: str, sequence: str) -> None:
        """Wait for each menu prompt, then inject the requested DTMF key."""
        keys = [key for key in sequence.split("w") if key]
        events: list[dict] = []
        fault = ""
        try:
            for key in keys:
                await self._start_realtime_stream(
                    call_id,
                    target_key=key,
                    menu_completion_ms=8000,
                    no_speech_timeout_ms=30000,
                )
                event = await asyncio.wait_for(
                    self._next_realtime_event(
                        call_id,
                        terminal_types={
                            "dtmf_ready",
                            "human_boundary",
                            "unknown_boundary",
                            "technical_unknown",
                            "asr_error",
                        },
                    ),
                    timeout=35,
                )
                events.append(event)
                await self._stop_realtime_stream(call_id)
                if event.get("event_type") != "dtmf_ready":
                    fault = str(event.get("event_type") or "realtime navigation failed")
                    await self.stop_call(call_id)
                    return
                await asyncio.to_thread(
                    self._run_api,
                    f"uuid_send_dtmf {call_id} {event['key']}",
                )
                logger.info("Realtime sent DTMF %s on call %s", event["key"], call_id)
                await asyncio.sleep(0.3)

            # Observe the resulting node without sending another key. A human
            # boundary stops immediately; a normal menu ends after silence.
            await self._start_realtime_stream(
                call_id,
                target_key=None,
                menu_completion_ms=12000,
                no_speech_timeout_ms=15000,
            )
            try:
                observation = await asyncio.wait_for(
                    self._next_realtime_event(
                        call_id,
                        terminal_types={
                            "human_boundary",
                            "unknown_boundary",
                            "technical_unknown",
                            "asr_error",
                        },
                    ),
                    timeout=20,
                )
            except TimeoutError:
                observation = {"event_type": "observation_timeout"}
            events.append(observation)
            await self._stop_realtime_stream(call_id)
            if observation.get("event_type") in (
                "human_boundary",
                "asr_error",
            ):
                fault = str(observation.get("event_type"))
            await self.stop_call(call_id)
        except Exception as exc:
            fault = f"{type(exc).__name__}: {exc}"
            logger.exception("Realtime navigation failed on call %s", call_id)
            try:
                await self._stop_realtime_stream(call_id)
            except Exception:
                pass
            await self.stop_call(call_id)
        finally:
            self._realtime_results[call_id] = {
                "fault": fault,
                "events": events,
            }

    async def _start_realtime_stream(
        self,
        call_id: str,
        *,
        target_key: str | None,
        menu_completion_ms: int,
        no_speech_timeout_ms: int,
    ) -> None:
        metadata = json.dumps(
            {
                "channel_uuid": call_id,
                "exploration_call_id": str(uuid_lib.uuid4()),
                "target_key": target_key,
                "encoding": "s16le",
                "sample_rate": 8000,
                "channels": 1,
                "remote_channel": 0,
                "silence_ms": 800,
                "menu_completion_ms": menu_completion_ms,
                "no_speech_timeout_ms": no_speech_timeout_ms,
                "gain": 1.0,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
        await asyncio.to_thread(
            self._run_api,
            f"uuid_audio_stream {call_id} start "
            f"{self.config.realtime_relay_url}/audio mono 8000 {metadata}",
        )

    async def _stop_realtime_stream(self, call_id: str) -> None:
        try:
            await asyncio.to_thread(
                self._run_api,
                f"uuid_audio_stream {call_id} stop",
            )
        except Exception:
            pass

    async def _next_realtime_event(
        self,
        call_id: str,
        *,
        terminal_types: set[str],
    ) -> dict:
        async with websockets.connect(
            f"{self.config.realtime_relay_url}/events",
            max_size=None,
        ) as websocket:
            async for raw in websocket:
                event = json.loads(raw)
                if event.get("channel_uuid") != call_id:
                    continue
                if event.get("event_type") in terminal_types:
                    return event
        raise RuntimeError("Realtime event stream closed")

    async def _send_dtmf_after_answer(self, call_id: str, sequence: str) -> None:
        """Replay a DTMF path onto the cellular call, mirroring the smoke test.

        Accepts the same `w`-separated compound format used elsewhere ("1w3").
        """
        await asyncio.sleep(self.config.dtmf_initial_delay)
        keys = [k for k in sequence.split("w") if k]
        for index, key in enumerate(keys):
            if index > 0:
                await asyncio.sleep(self.config.dtmf_key_gap)
            try:
                await asyncio.to_thread(self._run_api, f"uuid_send_dtmf {call_id} {key}")
                logger.info("Sent DTMF %s on call %s", key, call_id)
            except EslError as exc:
                logger.warning("Failed to send DTMF %s on %s: %s", key, call_id, exc)
                return

    async def _find_channel(self, correlation_id: str) -> str:
        """Resolve the dialplan leg using our unique outbound caller id."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.config.channel_lookup_timeout
        last_error: EslError | None = None

        while loop.time() < deadline:
            try:
                payload = await asyncio.to_thread(
                    self._run_api,
                    "show channels as json",
                )
                rows = self._channel_rows(payload)
                candidates = [
                    row
                    for row in rows
                    if str(row.get("cid_num")) == correlation_id
                ]
                if not candidates:
                    candidates = await self._channels_with_marker(
                        rows,
                        correlation_id,
                    )
                if candidates:
                    preferred = next(
                        (
                            row
                            for row in candidates
                            if str(row.get("direction")) == "inbound"
                        ),
                        candidates[0],
                    )
                    channel_id = str(preferred.get("uuid", ""))
                    if not channel_id:
                        continue
                    logger.info(
                        "Resolved correlation id %s to channel %s",
                        correlation_id,
                        channel_id,
                    )
                    return channel_id
            except EslError as exc:
                last_error = exc
            await asyncio.sleep(0.1)

        raise EslError(
            f"Could not find originated channel for {correlation_id}: {last_error}"
        )

    async def _channels_with_marker(
        self,
        rows: list[dict],
        correlation_id: str,
    ) -> list[dict]:
        """Compatibility fallback for endpoints that propagate custom variables."""
        candidates: list[dict] = []
        for row in rows:
            channel_id = str(row.get("uuid", ""))
            if not channel_id:
                continue
            try:
                marker = await asyncio.to_thread(
                    self._run_api,
                    f"uuid_getvar {channel_id} ivr_discovery_call_id",
                )
            except EslError:
                continue
            if marker.strip() == correlation_id:
                candidates.append(row)
        return candidates

    async def _start_recording(self, call_id: str, path: Path) -> None:
        """Attach the WAV recorder once the gateway leg is media-ready."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.config.recording_start_timeout
        last_error: EslError | None = None
        while loop.time() < deadline:
            if path.is_file():
                logger.info("Recording already active for call %s -> %s", call_id, path)
                return
            try:
                await asyncio.to_thread(
                    self._run_api,
                    f"uuid_record {call_id} start {path}",
                )
                for _ in range(10):
                    if path.is_file():
                        logger.info("Started recording call %s -> %s", call_id, path)
                        return
                    await asyncio.sleep(0.1)
            except EslError as exc:
                last_error = exc
            await asyncio.sleep(0.2)

        raise EslError(
            f"Could not start recording for {call_id}: {last_error}"
        )

    async def _play_voice_after_answer(self, call_id: str, path: Path) -> None:
        """Play a synthesized option phrase on the caller leg."""
        await asyncio.sleep(self.config.dtmf_initial_delay)
        try:
            await asyncio.to_thread(
                self._run_api,
                f"uuid_broadcast {call_id} {path} aleg",
            )
            logger.info("Played voice option on call %s from %s", call_id, path)
        except EslError as exc:
            logger.warning("Failed to play voice option on %s: %s", call_id, exc)

    async def _finalize_recording(self, call_id: str) -> CallResult:
        path = self._recording_path(call_id)
        deadline = asyncio.get_running_loop().time() + self.config.recording_finalize_timeout
        while not path.is_file() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.25)

        if not path.is_file():
            logger.warning("No recording file was produced for call %s", call_id)
            return CallResult(
                call_id=call_id,
                status=STATUS_COMPLETED,
                capabilities=self.capabilities,
            )
        return await self._transcribe_recording(call_id, path)

    async def _transcribe_recording(self, call_id: str, path: Path) -> CallResult:
        if not self.capabilities.transcript:
            return CallResult(
                call_id=call_id,
                status=STATUS_COMPLETED,
                capabilities=self.capabilities,
            )
        try:
            transcript = await self.audio_provider.transcribe(path)
        except AudioProviderError as exc:
            logger.error("ASR failed for call %s: %s", call_id, exc)
            return CallResult(
                call_id=call_id,
                status=STATUS_ERROR,
                capabilities=self.capabilities,
            )
        self._transcripts[call_id] = transcript
        return CallResult(
            call_id=call_id,
            status=STATUS_COMPLETED,
            transcript=transcript,
            capabilities=self.capabilities,
        )

    def _require_audio_capability(self) -> None:
        if not self.capabilities.transcript or not self.audio_provider.is_configured:
            raise ProviderCapabilityError(
                "Android SIM gateway requires a configured Audio Provider "
                "(Tencent: set TENCENTCLOUD_SECRET_ID and "
                "TENCENTCLOUD_SECRET_KEY)"
            )

    def _recording_path(self, call_id: str) -> Path:
        return Path(self.config.recording_dir) / f"{call_id}.wav"

    @staticmethod
    def _validate_recording_path(path: Path) -> None:
        if any(char in str(path) for char in ",{}\n\r\t "):
            raise ValueError(
                f"Recording path contains characters unsafe for FreeSWITCH: {path}"
            )

    def _run_api(self, command: str) -> str:
        with EslClient(self._esl_config) as client:
            return client.api(command)

    @staticmethod
    def _channel_active(channels_payload: str, call_id: str) -> bool:
        """Detect whether the given call id still has a live channel.

        FreeSWITCH's `show channels as json` returns a JSON object whose `rows`
        array contains one entry per channel. We avoid a hard dependency on the
        exact shape by falling back to a substring check.
        """
        channel_ids = AndroidSimGatewayProvider._channel_ids(channels_payload)
        if channel_ids:
            return call_id in channel_ids
        return call_id in channels_payload

    @staticmethod
    def _channel_ids(channels_payload: str) -> list[str]:
        """Extract channel UUIDs from FreeSWITCH's JSON channel listing."""
        return [
            str(row.get("uuid"))
            for row in AndroidSimGatewayProvider._channel_rows(channels_payload)
            if row.get("uuid")
        ]

    @staticmethod
    def _channel_rows(channels_payload: str) -> list[dict]:
        """Parse FreeSWITCH's JSON channel listing into row dictionaries."""
        import json

        try:
            data = json.loads(channels_payload)
        except (ValueError, TypeError):
            return []

        rows = data.get("rows") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]
