"""Audio framing helpers for the realtime relay."""

from __future__ import annotations

import math
from array import array


class AudioFormatError(ValueError):
    pass


def decode_to_mono_pcm(
    payload: bytes,
    *,
    encoding: str,
    channels: int,
    remote_channel: int = 0,
) -> bytes:
    """Convert supported interleaved audio into little-endian mono PCM16."""
    if channels < 1:
        raise AudioFormatError("channels must be at least 1")
    if remote_channel < 0 or remote_channel >= channels:
        raise AudioFormatError(
            f"remote_channel {remote_channel} is outside 0..{channels - 1}"
        )

    bytes_per_sample = 2
    frame_bytes = bytes_per_sample * channels
    if not payload or len(payload) % frame_bytes != 0:
        raise AudioFormatError(
            f"audio payload size {len(payload)} is not aligned to frame size {frame_bytes}"
        )

    samples = array("h")
    samples.frombytes(payload)
    normalized_encoding = encoding.lower()
    if normalized_encoding in ("l16be", "s16be", "pcm_s16be"):
        samples.byteswap()
    elif normalized_encoding not in ("s16le", "l16le", "pcm_s16le"):
        raise AudioFormatError(f"unsupported audio encoding: {encoding}")

    if channels == 1:
        return samples.tobytes()
    return samples[remote_channel::channels].tobytes()


def resample_pcm16_mono(
    payload: bytes,
    *,
    source_rate: int,
    target_rate: int,
) -> bytes:
    """Resample mono PCM16 using simple integer-rate conversion."""
    if source_rate == target_rate:
        return payload
    if source_rate <= 0 or target_rate <= 0:
        raise AudioFormatError("sample rates must be positive")

    samples = array("h")
    samples.frombytes(payload)
    if target_rate == source_rate * 2:
        output = array("h")
        for sample in samples:
            output.append(sample)
            output.append(sample)
        return output.tobytes()
    if source_rate == target_rate * 2:
        return samples[::2].tobytes()
    raise AudioFormatError(
        f"unsupported sample-rate conversion {source_rate} -> {target_rate}"
    )


def pcm16_rms(payload: bytes) -> float:
    """Return RMS amplitude for mono PCM16 audio."""
    if not payload:
        return 0.0
    samples = array("h")
    samples.frombytes(payload)
    if not samples:
        return 0.0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


def apply_gain_pcm16(payload: bytes, gain: float) -> bytes:
    """Apply linear gain with PCM16 clipping."""
    if gain == 1.0:
        return payload
    if gain < 0:
        raise AudioFormatError("gain must be non-negative")
    samples = array("h")
    samples.frombytes(payload)
    output = array(
        "h",
        (
            max(-32768, min(32767, int(sample * gain)))
            for sample in samples
        ),
    )
    return output.tobytes()
