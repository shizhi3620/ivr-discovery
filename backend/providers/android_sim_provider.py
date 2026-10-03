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
import time
import uuid as uuid_lib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import websockets

from audio import AudioProvider, AudioProviderError, get_audio_provider
import recording_retention
from providers.base import (
    CallResult,
    NO_INPUT_TOKEN,
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

# /tmp loses recordings on reboot, which silently voided the ADR 0029/0042
# retention windows. Keep them next to the backend by default (gitignored).
_DEFAULT_RECORDING_DIR = str(Path(__file__).resolve().parents[1] / "recordings")


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
    dtmf_fallback_delay_ms: int = 16000
    # FreeSWITCH writes one WAV per call into this directory.
    recording_dir: str = _DEFAULT_RECORDING_DIR
    # Wait briefly for bgapi originate to create a visible channel.
    channel_lookup_timeout: float = 10.0
    # Wait this long for the gateway leg to accept uuid_record.
    recording_start_timeout: float = 30.0
    # Wait briefly for FreeSWITCH to finish flushing the WAV after hangup.
    recording_finalize_timeout: float = 5.0
    # Shadow-judgment veto window; backend enforces the hangup deadline.
    boundary_cancel_window_ms: int = 1500
    automated_notice_extension_ms: int = 10000
    # Deep-observation window for the node reached at the end of a DTMF path.
    # Must be long enough to hear at least two rounds of IVR timeout reminders.
    observation_menu_completion_ms: int = 90000
    observation_no_speech_ms: int = 90000
    observation_timeout: float = 95.0
    # Navigation: how long a hold/quality phrase may be followed by the real
    # menu before it is treated as a terminal human boundary.
    human_boundary_menu_grace_ms: int = 12000
    # No-input timeout probing (ADR 0041). On branch calls, hold the target key
    # at the final menu and stay silent until the first "press any key"
    # reminder, answer it, then continue navigation ("hitchhike" evidence). A
    # path ending in the no-input token runs a dedicated probe that keeps
    # listening until the IVR hangs up.
    timeout_probe_enabled: bool = False
    # Key sent in answer to a "press any key" reminder. "0" often means
    # operator and the menu keys are real options, so use a neutral digit.
    any_key_value: str = "9"
    # How long the hitchhike phase waits for the first reminder before giving
    # up and pressing the target key anyway.
    any_key_prompt_wait_s: float = 75.0
    # Upper bound for a dedicated no-input probe call waiting for the IVR to
    # hang up on its own.
    # Two full no-input cycles (menu replays, reminders, keypress answer,
    # second cycle to remote hangup) need roughly 4 minutes of observation.
    # Navigation (~45 s) + this budget must stay under ~312 s so the
    # mono-downmixed recording fits Tencent's 5 MB file-ASR limit.
    timeout_probe_max_s: float = 260.0

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
            dtmf_fallback_delay_ms=int(
                source.get("CALL_DTMF_FALLBACK_DELAY_MS", "16000")
            ),
            recording_dir=source.get("CALL_RECORDING_DIR", _DEFAULT_RECORDING_DIR),
            channel_lookup_timeout=float(
                source.get("CALL_CHANNEL_LOOKUP_TIMEOUT", "10")
            ),
            recording_start_timeout=float(
                source.get("CALL_RECORDING_START_TIMEOUT", "30")
            ),
            recording_finalize_timeout=float(
                source.get("CALL_RECORDING_FINALIZE_TIMEOUT", "5")
            ),
            boundary_cancel_window_ms=int(
                source.get("SHADOW_BOUNDARY_CANCEL_WINDOW_MS", "1500")
            ),
            automated_notice_extension_ms=int(
                source.get("SHADOW_AUTOMATED_NOTICE_EXTENSION_MS", "10000")
            ),
            observation_menu_completion_ms=int(
                source.get("CALL_OBSERVATION_MENU_COMPLETION_MS", "90000")
            ),
            observation_no_speech_ms=int(
                source.get("CALL_OBSERVATION_NO_SPEECH_MS", "90000")
            ),
            observation_timeout=float(
                source.get("CALL_OBSERVATION_TIMEOUT", "95")
            ),
            human_boundary_menu_grace_ms=int(
                source.get("CALL_HUMAN_BOUNDARY_MENU_GRACE_MS", "12000")
            ),
            timeout_probe_enabled=source.get(
                "CALL_TIMEOUT_PROBE_ENABLED",
                "0",
            ).lower()
            in ("1", "true", "yes"),
            any_key_value=source.get("CALL_ANY_KEY_VALUE", "9"),
            any_key_prompt_wait_s=float(
                source.get("CALL_ANY_KEY_PROMPT_WAIT_S", "75")
            ),
            timeout_probe_max_s=float(
                source.get("CALL_TIMEOUT_PROBE_MAX_S", "150")
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
        self._classify_call_recording(call_id, result)
        return result

    def mark_call_recording(self, call_id: str, retention_class: str) -> None:
        """Override a call recording's retention class (ADR 0029/0042).

        The retention module enforces stricter-wins, so a discovery-layer
        "human" marking always prevails over an earlier provider guess.
        """
        recording_path = self._recording_path(call_id)
        if not recording_path.is_file():
            return
        try:
            recording_retention.classify_recording(
                Path(self.config.recording_dir),
                recording_path.name,
                retention_class,
            )
        except Exception as exc:  # noqa: BLE001 - retention never breaks a call
            logger.warning(
                "Failed to mark recording %s as %s: %s",
                recording_path.name,
                retention_class,
                exc,
            )

    def _classify_call_recording(self, call_id: str, result: CallResult) -> None:
        realtime = self._realtime_results.get(call_id) or {}
        events = realtime.get("events") or []
        event_types = {
            event.get("event_type")
            for event in events
            if isinstance(event, dict)
        }
        retention_class = recording_retention.CLASS_MENU
        if (
            "human_boundary" in event_types
            or result.realtime_fault == "human_boundary"
        ):
            retention_class = recording_retention.CLASS_HUMAN
        elif realtime.get("no_input_probe") or result.realtime_fault:
            # Exploration evidence (ADR 0042): timeout probes, failed
            # navigation, unexpected terminals.
            retention_class = recording_retention.CLASS_EVIDENCE
        self.mark_call_recording(call_id, retention_class)

    async def get_call(self, call_id: str) -> CallResult:
        result = await self._get_call(call_id)
        realtime = self._realtime_results.get(call_id)
        if realtime:
            result.realtime_fault = str(realtime.get("fault") or "")
        return result

    async def _get_call(self, call_id: str) -> CallResult:
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
        tokens = [key for key in sequence.split("w") if key]
        dedicated_probe = bool(tokens) and tokens[-1] == NO_INPUT_TOKEN
        keys = tokens[:-1] if dedicated_probe else tokens
        events: list[dict] = []
        fault = ""
        try:
            for key_index, key in enumerate(keys):
                event = await self._await_navigation_event(
                    call_id, key, key_index=key_index, keys=keys
                )
                events.append(event)
                if event.get("event_type") not in (
                    "dtmf_ready",
                    "timed_fallback",
                ):
                    fault = str(event.get("event_type") or "realtime navigation failed")
                    await self.stop_call(call_id)
                    return
                is_last_key = key_index == len(keys) - 1
                if (
                    is_last_key
                    and not dedicated_probe
                    and self.config.timeout_probe_enabled
                ):
                    # Hitchhike (ADR 0041): hold the target key, stay silent
                    # until the first "press any key" reminder, answer it, then
                    # re-acquire the replayed menu before pressing the key.
                    reminder = await self._await_any_key_prompt(call_id)
                    events.append(reminder)
                    if reminder.get("event_type") == "any_key_prompt":
                        await asyncio.to_thread(
                            self._run_api,
                            f"uuid_send_dtmf {call_id} {self.config.any_key_value}",
                        )
                        logger.info(
                            "Answered any-key reminder with %s on call %s",
                            self.config.any_key_value,
                            call_id,
                        )
                        events.append(
                            {
                                "event_type": "any_key_sent",
                                "key": self.config.any_key_value,
                            }
                        )
                        await asyncio.sleep(0.3)
                        event = await self._await_navigation_event(
                            call_id, key, key_index=key_index, keys=keys
                        )
                        events.append(event)
                        if event.get("event_type") not in (
                            "dtmf_ready",
                            "timed_fallback",
                        ):
                            fault = str(
                                event.get("event_type")
                                or "realtime navigation failed after any-key"
                            )
                            await self.stop_call(call_id)
                            return
                await asyncio.to_thread(
                    self._run_api,
                    f"uuid_send_dtmf {call_id} {event['key']}",
                )
                logger.info("Realtime sent DTMF %s on call %s", event["key"], call_id)
                await asyncio.sleep(0.3)

            if dedicated_probe:
                # Dedicated no-input probe (ADR 0041): stay silent and keep
                # listening until the IVR ends the call itself.
                observation = await self._observe_until_remote_hangup(call_id)
                events.append(observation)
                if observation.get("event_type") in (
                    "technical_unknown",
                    "asr_error",
                ):
                    fault = str(observation.get("event_type"))
                await self.stop_call(call_id)
            else:
                # Observe the resulting node without sending another key. Both the
                # Mandarin and the English submenus may announce a hold/quality
                # message, then a real menu, then repeated timeout reminders, so
                # keep a long observation window and do not treat a hold phrase as
                # a terminal human boundary during this phase.
                observation_menu_ms = self.config.observation_menu_completion_ms
                observation_no_speech_ms = self.config.observation_no_speech_ms
                observation_timeout = self.config.observation_timeout
                await self._start_realtime_stream(
                    call_id,
                    target_key=None,
                    menu_completion_ms=observation_menu_ms,
                    no_speech_timeout_ms=observation_no_speech_ms,
                    allow_target_key_fallback=False,
                    hold_through_human_boundary=True,
                    detect_any_key_prompt=self.config.timeout_probe_enabled,
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
                                "stream_stopped",
                            },
                        ),
                        timeout=observation_timeout,
                    )
                except Exception as exc:
                    observation = {
                        "event_type": "observation_timeout",
                        "error": str(exc),
                    }
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
                "no_input_probe": dedicated_probe,
            }

    async def _await_navigation_event(
        self,
        call_id: str,
        key: str,
        *,
        key_index: int,
        keys: list[str],
    ) -> dict:
        """Open a navigation stream for one key and wait for its decision."""
        await self._start_realtime_stream(
            call_id,
            target_key=key,
            menu_completion_ms=15000,
            no_speech_timeout_ms=30000,
            allow_target_key_fallback=(key == "2"),
            human_boundary_menu_grace_ms=(
                self.config.human_boundary_menu_grace_ms
            ),
        )
        terminal_types = {
            "dtmf_ready",
            "human_boundary",
            "unknown_boundary",
            "technical_unknown",
            "asr_error",
        }
        fallback_for_key = (
            key == "2"
            and key_index == 1
            and keys[0] == "1"
        )
        timeout = (
            self.config.dtmf_fallback_delay_ms / 1000
            if fallback_for_key
            else 35
        )
        try:
            event = await asyncio.wait_for(
                self._next_realtime_event(
                    call_id,
                    terminal_types=terminal_types,
                ),
                timeout=timeout,
            )
        except TimeoutError:
            if not fallback_for_key:
                raise
            logger.warning(
                "Timed DTMF fallback for known 1w2 path after %d ms",
                self.config.dtmf_fallback_delay_ms,
            )
            event = {
                "event_type": "timed_fallback",
                "key": key,
            }
        finally:
            await self._stop_realtime_stream(call_id)
        return event

    async def _await_any_key_prompt(self, call_id: str) -> dict:
        """Listen silently for the first "press any key" reminder (ADR 0041)."""
        await self._start_realtime_stream(
            call_id,
            target_key=None,
            menu_completion_ms=self.config.observation_menu_completion_ms,
            no_speech_timeout_ms=self.config.observation_no_speech_ms,
            allow_target_key_fallback=False,
            hold_through_human_boundary=True,
            detect_any_key_prompt=True,
        )
        try:
            event = await asyncio.wait_for(
                self._next_realtime_event(
                    call_id,
                    terminal_types={
                        "any_key_prompt",
                        "unknown_boundary",
                        "technical_unknown",
                        "asr_error",
                        "stream_stopped",
                    },
                ),
                timeout=self.config.any_key_prompt_wait_s,
            )
        except Exception as exc:
            event = {"event_type": "any_key_prompt_timeout", "error": str(exc)}
        finally:
            await self._stop_realtime_stream(call_id)
        return event

    async def _observe_until_remote_hangup(self, call_id: str) -> dict:
        """Dedicated no-input probe (ADR 0041).

        Stay silent through the menu replays and the first "press any key"
        reminder; answer the final reminder (仍然听不见…) with one keypress,
        then stay silent again until the IVR hangs up. The keypress probes how
        the IVR behaves when a caller responds at the last moment.
        """
        await self._start_realtime_stream(
            call_id,
            target_key=None,
            menu_completion_ms=self.config.observation_menu_completion_ms,
            no_speech_timeout_ms=self.config.observation_no_speech_ms,
            allow_target_key_fallback=False,
            hold_through_human_boundary=True,
            detect_any_key_prompt=True,
            detect_final_reminder=True,
        )
        started = time.monotonic()
        budget = self.config.timeout_probe_max_s
        answered = False
        try:
            event = await asyncio.wait_for(
                self._next_realtime_event(
                    call_id,
                    terminal_types={
                        "final_any_key_prompt",
                        "stream_stopped",
                        "technical_unknown",
                        "asr_error",
                    },
                ),
                timeout=budget,
            )
            if event.get("event_type") == "final_any_key_prompt":
                answered = True
                logger.info(
                    "No-input probe answering final reminder with key %s on call %s",
                    self.config.any_key_value,
                    call_id,
                )
                await asyncio.to_thread(
                    self._run_api,
                    f"uuid_send_dtmf {call_id} {self.config.any_key_value}",
                )
                remaining = budget - (time.monotonic() - started)
                event = await asyncio.wait_for(
                    self._next_realtime_event(
                        call_id,
                        terminal_types={
                            "stream_stopped",
                            "technical_unknown",
                            "asr_error",
                        },
                    ),
                    timeout=max(1.0, remaining),
                )
                event["answered_final_reminder"] = True
        except Exception as exc:
            event = {
                "event_type": "timeout_probe_timeout",
                "error": str(exc),
                "answered_final_reminder": answered,
            }
        finally:
            await self._stop_realtime_stream(call_id)
        if event.get("event_type") == "stream_stopped":
            logger.info(
                "No-input probe captured remote hangup on call %s (answered=%s)",
                call_id,
                event.get("answered_final_reminder", False),
            )
        return event

    async def _start_realtime_stream(
        self,
        call_id: str,
        *,
        target_key: str | None,
        menu_completion_ms: int,
        no_speech_timeout_ms: int,
        allow_target_key_fallback: bool,
        hold_through_human_boundary: bool = False,
        human_boundary_menu_grace_ms: int = 0,
        detect_any_key_prompt: bool = False,
        detect_final_reminder: bool = False,
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
                "allow_target_key_fallback": allow_target_key_fallback,
                "hold_through_human_boundary": hold_through_human_boundary,
                "human_boundary_menu_grace_ms": human_boundary_menu_grace_ms,
                "detect_any_key_prompt": detect_any_key_prompt,
                "detect_final_reminder": detect_final_reminder,
                "boundary_cancel_window_ms": self.config.boundary_cancel_window_ms,
                "automated_notice_extension_ms": (
                    self.config.automated_notice_extension_ms
                ),
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

    async def _next_realtime_event(
        self,
        call_id: str,
        *,
        terminal_types: set[str],
    ) -> dict:
        """Return the next terminal realtime event for ``call_id``.

        While waiting, this also enforces the backend-side guarantee for the
        shadow-judgment veto window: when a ``boundary_pending`` event arrives,
        a local timer is armed to kill the call at its deadline. A subsequent
        ``boundary_cancelled`` disarms it. This keeps the call from hanging even
        if the relay process dies or never emits a follow-up terminal event.
        """
        deadline_task: asyncio.Task | None = None
        try:
            async with websockets.connect(
                f"{self.config.realtime_relay_url}/events",
                max_size=None,
            ) as websocket:
                while True:
                    recv_task = asyncio.create_task(websocket.recv())
                    waiters: set[asyncio.Task] = {recv_task}
                    if deadline_task is not None:
                        waiters.add(deadline_task)
                    done, _ = await asyncio.wait(
                        waiters,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if deadline_task is not None and deadline_task in done:
                        recv_task.cancel()
                        logger.warning(
                            "boundary_pending deadline reached for call %s; killing",
                            call_id,
                        )
                        await self.stop_call(call_id)
                        return {
                            "event_type": "boundary_pending_timeout",
                            "channel_uuid": call_id,
                        }
                    if recv_task not in done:
                        continue

                    raw = recv_task.result()
                    event = json.loads(raw)
                    if event.get("channel_uuid") != call_id:
                        continue
                    event_type = event.get("event_type")
                    if event_type == "boundary_pending":
                        if deadline_task is not None and not deadline_task.done():
                            deadline_task.cancel()
                        deadline_task = asyncio.create_task(
                            asyncio.sleep(self._boundary_deadline_delay(event))
                        )
                        continue
                    if event_type == "boundary_cancelled":
                        if deadline_task is not None and not deadline_task.done():
                            deadline_task.cancel()
                            deadline_task = None
                        continue
                    if event_type in terminal_types:
                        return event
        finally:
            if deadline_task is not None and not deadline_task.done():
                deadline_task.cancel()
                await asyncio.gather(deadline_task, return_exceptions=True)

    @staticmethod
    def _boundary_deadline_delay(event: dict) -> float:
        raw = event.get("deadline_at")
        if isinstance(raw, str):
            try:
                deadline = datetime.fromisoformat(raw)
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)
                return max(0.0, (deadline - datetime.now(timezone.utc)).total_seconds())
            except ValueError:
                pass
        return 1.5

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
