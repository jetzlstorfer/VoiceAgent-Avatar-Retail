import base64
from typing import Iterable

import numpy as np


TARGET_SAMPLE_RATE = 24000
INT16_MAX = np.iinfo(np.int16).max
INT16_MIN = np.iinfo(np.int16).min


def float_frame_to_pcm16_bytes(frame: Iterable[float]) -> bytes:
    """Convert an iterable of float32 samples (-1.0 to 1.0) into PCM16 bytes."""
    float_array = np.asarray(frame, dtype=np.float32)
    clipped = np.clip(float_array, -1.0, 1.0)
    int16_samples = (clipped * INT16_MAX).astype(np.int16)
    return int16_samples.tobytes()


def pcm16_bytes_to_base64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def base64_to_uint8_array(data_b64: str) -> np.ndarray:
    raw = base64.b64decode(data_b64)
    return np.frombuffer(raw, dtype=np.uint8)


def float_frame_base64_to_pcm16_base64(data_b64: str) -> str:
    """Accept base64-encoded float32 bytes and return base64-encoded PCM16."""
    float_array = np.frombuffer(base64.b64decode(data_b64), dtype=np.float32)
    pcm_bytes = float_frame_to_pcm16_bytes(float_array)
    return base64.b64encode(pcm_bytes).decode("ascii")
