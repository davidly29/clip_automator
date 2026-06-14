"""Capture Engine (design spec §4.3).

frames mode (default): screenshot the video element's bounding box at each
interval. Lightweight, no extra binaries, sufficient for verification.

clip mode: assemble sampled frames into an mp4 via ffmpeg if it is available;
otherwise we keep the frames and record that the clip could not be encoded
(graceful degradation rather than failing the whole check).
"""

from __future__ import annotations

import asyncio
import io
import shutil
import subprocess
import time
from pathlib import Path

from PIL import Image

from .browser import VideoHandle
from .config import CaptureConfig
from .logging_setup import get_logger
from .models import Frame


def ffmpeg_exe() -> str | None:
    """Resolve an ffmpeg binary.

    Prefers the binary bundled by the ``imageio-ffmpeg`` package (installed as a
    dependency, so no system install is required), then falls back to an ffmpeg
    on PATH.
    """
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe:
            return exe
    except Exception:  # noqa: BLE001 - fall back to PATH
        pass
    return shutil.which("ffmpeg")


def _maybe_downscale(png: bytes, width: int | None, height: int | None) -> bytes:
    if width is None and height is None:
        return png
    with Image.open(io.BytesIO(png)) as im:
        w = width or im.width
        h = height or im.height
        resized = im.resize((max(1, w), max(1, h)))
        out = io.BytesIO()
        resized.save(out, format="PNG")
        return out.getvalue()


class CaptureEngine:
    def __init__(self, cfg: CaptureConfig):
        self._cfg = cfg
        self._log = get_logger()

    async def capture_frames(self, video: VideoHandle) -> list[Frame]:
        """Sample N screenshots of the element region over the capture window."""
        n = self._cfg.planned_frame_count()
        interval = self._cfg.interval_ms / 1000.0
        if self._cfg.frame_count is not None:
            # When frame_count is fixed, spread it across the duration window.
            interval = self._cfg.duration_s / max(1, n - 1)

        frames: list[Frame] = []
        start = time.monotonic()
        for i in range(n):
            t_ms = int((time.monotonic() - start) * 1000)
            try:
                png = await video.locator.screenshot(timeout=5000)
            except Exception as e:  # noqa: BLE001 - tolerate transient shots
                self._log.debug("frame %d screenshot failed: %s", i, e)
                continue
            png = _maybe_downscale(png, self._cfg.width, self._cfg.height)
            frames.append(Frame(index=len(frames), t_ms=t_ms, png=png))
            if i < n - 1:
                await asyncio.sleep(interval)
        self._log.debug("captured %d/%d frames", len(frames), n)
        return frames

    def finalize_clip(
        self,
        recorded_video: Path,
        out_dir: Path,
        start_offset_s: float = 0.0,
        clip_duration_s: float | None = None,
    ) -> Path | None:
        """Turn a Playwright recording into the clip artifact (actual video).

        Playwright records the whole context lifetime (it cannot start/stop
        mid-session), so to make the clip *begin in fullscreen* we trim it to the
        capture window: skip ``start_offset_s`` (everything before fullscreen +
        playback began) and keep ``clip_duration_s`` of footage.

        Transcodes to snippet.mp4 with ffmpeg. If ffmpeg is unavailable, the raw
        .webm is kept as snippet.webm so an artifact still exists.
        """
        if not recorded_video or not recorded_video.exists():
            self._log.warning("no recorded video to finalize")
            return None

        exe = ffmpeg_exe()
        if not exe:
            fallback = out_dir / "snippet.webm"
            shutil.copyfile(recorded_video, fallback)
            self._log.warning("ffmpeg not found; kept raw recording as %s", fallback.name)
            return fallback

        out_path = out_dir / "snippet.mp4"
        cmd = [exe, "-y"]
        # Seek before -i for a fast, re-encoded accurate-enough cut.
        if start_offset_s and start_offset_s > 0.05:
            cmd += ["-ss", f"{start_offset_s:.3f}"]
        cmd += ["-i", str(recorded_video)]
        if clip_duration_s and clip_duration_s > 0:
            cmd += ["-t", f"{clip_duration_s:.3f}"]
        cmd += [
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            str(out_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            self._log.warning("ffmpeg transcode failed: %s", proc.stderr.strip()[:400])
            fallback = out_dir / "snippet.webm"
            shutil.copyfile(recorded_video, fallback)
            return fallback
        self._log.info("clip trimmed to fullscreen window (offset=%.2fs, dur=%s)",
                       start_offset_s, f"{clip_duration_s:.2f}s" if clip_duration_s else "full")
        return out_path
