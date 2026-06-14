"""Shared test helpers."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from vpv.models import Frame, MediaSnapshot


def png_bytes(arr: np.ndarray) -> bytes:
    """Encode a uint8 HxW or HxWx3 array as PNG bytes."""
    im = Image.fromarray(arr.astype(np.uint8))
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def moving_frames(n: int = 6, size: int = 64) -> list[Frame]:
    """Frames with a bright square that moves each step (clear motion)."""
    frames = []
    for i in range(n):
        a = np.zeros((size, size), dtype=np.uint8)
        x = (i * 7) % (size - 10)
        a[10:20, x:x + 10] = 255
        frames.append(Frame(index=i, t_ms=i * 500, png=png_bytes(a)))
    return frames


def frozen_frames(n: int = 6, size: int = 64) -> list[Frame]:
    """Identical non-blank frames (a static picture, not black)."""
    a = np.zeros((size, size), dtype=np.uint8)
    a[10:40, 10:40] = 200  # constant content => high variance, zero motion
    return [Frame(index=i, t_ms=i * 500, png=png_bytes(a)) for i in range(n)]


def blank_frames(n: int = 6, size: int = 64) -> list[Frame]:
    """All-black frames (no rendered content)."""
    a = np.zeros((size, size), dtype=np.uint8)
    return [Frame(index=i, t_ms=i * 500, png=png_bytes(a)) for i in range(n)]


def snapshot(
    current_time: float = 0.0,
    *,
    paused: bool = False,
    ended: bool = False,
    ready_state: int = 4,
    error_code: int | None = None,
) -> MediaSnapshot:
    return MediaSnapshot(
        current_time=current_time,
        duration=120.0,
        paused=paused,
        ended=ended,
        ready_state=ready_state,
        network_state=2,
        error_code=error_code,
        video_width=640,
        video_height=360,
    )
