"""Minimal FreeSWITCH Event Socket Library (ESL) client.

Only the handful of commands the Android SIM gateway provider needs are
implemented: authenticate, run a blocking API command, and run a background API
command. This avoids a heavyweight dependency while still talking real ESL.
"""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class EslError(RuntimeError):
    pass


@dataclass
class EslConfig:
    host: str = "127.0.0.1"
    port: int = 8021
    password: str = ""

    @classmethod
    def from_env(cls, env: dict | None = None) -> "EslConfig":
        import os

        source = env if env is not None else os.environ
        return cls(
            host=source.get("FREESWITCH_ESL_HOST", "127.0.0.1"),
            port=int(source.get("FREESWITCH_ESL_PORT", "8021")),
            password=source.get("FREESWITCH_ESL_PASSWORD", ""),
        )


class EslClient:
    """Blocking ESL client. Intended for short command/reply exchanges.

    Callers that must not block the event loop should run these calls in a
    thread (the provider does this via asyncio.to_thread).
    """

    def __init__(self, config: EslConfig, timeout: float = 10.0):
        self.config = config
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buf = b""

    # -- connection lifecycle -------------------------------------------------

    def connect(self) -> None:
        if self._sock is not None:
            return
        sock = socket.create_connection((self.config.host, self.config.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        self._sock = sock
        banner = self._read_message()
        if "auth/request" not in banner.lower():
            raise EslError(f"Unexpected ESL banner: {banner!r}")
        reply = self._command(f"auth {self.config.password}")
        if not self._reply_text(reply).startswith("+OK"):
            raise EslError(f"ESL auth failed: {reply!r}")

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
                self._buf = b""

    def __enter__(self) -> "EslClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- commands -------------------------------------------------------------

    def api(self, command: str) -> str:
        """Run a blocking API command and return the body of its reply."""
        reply = self._command(f"api {command}")
        body = self._extract_body(reply)
        # A rejected command comes back as an error reply rather than payload.
        text = self._reply_text(reply)
        if text.startswith("-ERR"):
            raise EslError(f"ESL command failed: {text}")
        return body

    def bgapi(self, command: str) -> str:
        """Run a background API command and return its Job-UUID."""
        reply = self._command(f"bgapi {command}")
        text = self._reply_text(reply)
        if not text.startswith("+OK"):
            raise EslError(f"bgapi failed: {text!r}")
        for line in reply.splitlines():
            if line.lower().startswith("job-uuid:"):
                return line.split(":", 1)[1].strip()
        raise EslError(f"No Job-UUID in bgapi reply: {reply!r}")

    # -- internals ------------------------------------------------------------

    @staticmethod
    def _reply_text(reply: str) -> str:
        """Return the value of the ESL `Reply-Text` header, if present.

        FreeSWITCH wraps command results as
        `Content-Type: command/reply\nReply-Text: +OK ...`.
        """
        for raw in reply.splitlines():
            line = raw.strip()
            if line.lower().startswith("reply-text:"):
                return line.split(":", 1)[1].strip()
        return reply.strip()

    @staticmethod
    def _extract_body(reply: str) -> str:
        """Strip the ESL reply headers, returning just the payload."""
        if not reply.startswith("Content-Type:"):
            return reply.strip()
        # Headers are separated from the body by a blank line.
        parts = reply.split("\n\n", 1)
        if len(parts) == 2:
            return parts[1].strip()
        return reply.strip()

    def _command(self, command: str) -> str:
        if self._sock is None:
            raise EslError("ESL client is not connected")
        self._sock.sendall(command.encode("utf-8") + b"\n\n")
        return self._read_message()

    def _read_message(self) -> str:
        if self._sock is None:
            raise EslError("ESL client is not connected")
        while True:
            if b"\n\n" in self._buf:
                head, _, rest = self._buf.partition(b"\n\n")
                content_length = self._content_length(head)
                if content_length is None:
                    self._buf = rest
                    return head.decode("utf-8", "replace")
                if len(rest) >= content_length:
                    body = rest[:content_length]
                    self._buf = rest[content_length:]
                    return head.decode("utf-8", "replace") + "\n\n" + body.decode("utf-8", "replace")
            chunk = self._sock.recv(4096)
            if not chunk:
                raise EslError("ESL connection closed by peer")
            self._buf += chunk

    @staticmethod
    def _content_length(head: bytes) -> int | None:
        for raw in head.split(b"\n"):
            line = raw.decode("utf-8", "replace").strip()
            if line.lower().startswith("content-length:"):
                try:
                    return int(line.split(":", 1)[1].strip())
                except ValueError:
                    return None
        return None
