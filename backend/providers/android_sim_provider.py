"""Android SIM gateway provider (China mainland path).

Places real cellular calls through a rooted Android phone running
`s-xander/Android-sip-gateway`, registered to a local FreeSWITCH. The provider
talks to FreeSWITCH over ESL, originates a call to the registered gateway
extension, and passes the GSM destination in the `X-GSM-Destination` SIP header
(see gateway/freeswitch/README.md).

This provider deliberately advertises `transcript=False`: the SIM gateway only
carries audio. ASR/TTS are a separate stage (docs/adr/0004) and are not wired in
yet, so callers must not expect a transcript from this provider.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid as uuid_lib
from dataclasses import dataclass

from providers.base import (
    CallResult,
    ProviderCapabilities,
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

    @classmethod
    def from_env(cls, env: dict | None = None) -> "GatewayConfig":
        source = env if env is not None else os.environ
        esl = EslConfig.from_env(source)
        return cls(
            esl_host=esl.host,
            esl_port=esl.port,
            esl_password=esl.password,
            domain=source.get("FREESWITCH_DOMAIN", source.get("FREESWITCH_ESL_HOST", "127.0.0.1")),
            gateway_extension=source.get("SIP_GATEWAY_EXTENSION", "gateway1"),
            caller_extension=source.get("SIP_SOFTPHONE_EXTENSION", "softphone"),
        )


class AndroidSimGatewayProvider:
    name = "android_sim"
    capabilities = ProviderCapabilities(transcript=False, speech=False, dtmf=True)

    def __init__(self, config: GatewayConfig | None = None):
        self.config = config or GatewayConfig.from_env()
        self._esl_config = EslConfig(
            host=self.config.esl_host,
            port=self.config.esl_port,
            password=self.config.esl_password,
        )

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
        if task is not None or voice_option:
            raise NotImplementedError(
                "Android SIM gateway has no on-call speech agent; ASR/TTS navigation "
                "is a separate stage and is not wired in yet."
            )

        call_id = str(uuid_lib.uuid4())
        dest = phone_number.strip()
        if not dest:
            raise ValueError("phone_number is required")

        originate = (
            "originate "
            "{"
            f"origination_uuid={call_id},"
            f"origination_caller_id_number={self.config.caller_extension},"
            "ignore_early_media=true,"
            f"sip_h_X-GSM-Destination={dest}"
            f"}}user/{self.config.gateway_extension}@{self.config.domain} "
            "&park()"
        )

        await asyncio.to_thread(self._run_api, f"bgapi {originate}")

        # Enforce max_duration: schedule a hangup so a parked call cannot live forever.
        schedule = f"sched_api +{int(max_duration)} {call_id} uuid_kill {call_id}"
        try:
            await asyncio.to_thread(self._run_api, schedule)
        except EslError:
            logger.warning("Could not schedule max-duration hangup for %s", call_id)

        if dtmf_sequence:
            asyncio.create_task(self._send_dtmf_after_answer(call_id, dtmf_sequence))

        logger.info("Originated GSM call %s -> %s", call_id, dest)
        return call_id

    async def wait_for_call(
        self,
        call_id: str,
        on_transcript: TranscriptCallback | None = None,
    ) -> CallResult:
        # This provider never produces a transcript. Callers that need one must
        # run ASR on the captured audio separately.
        result = await self.get_call(call_id)
        while result.status == STATUS_IN_PROGRESS:
            await asyncio.sleep(2.0)
            result = await self.get_call(call_id)
        return result

    async def get_call(self, call_id: str) -> CallResult:
        try:
            channels = await asyncio.to_thread(self._run_api, "show channels as json")
        except EslError as exc:
            logger.warning("ESL query failed for %s: %s", call_id, exc)
            return CallResult(call_id=call_id, status=STATUS_ERROR, capabilities=self.capabilities)

        active = self._channel_active(channels, call_id)
        return CallResult(
            call_id=call_id,
            status=STATUS_IN_PROGRESS if active else STATUS_COMPLETED,
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
        import json

        try:
            data = json.loads(channels_payload)
        except (ValueError, TypeError):
            return call_id in channels_payload

        rows = data.get("rows") if isinstance(data, dict) else None
        if rows is None:
            return call_id in channels_payload
        for row in rows:
            values = row.values() if isinstance(row, dict) else row
            if any(str(v) == call_id for v in values):
                return True
        return False
