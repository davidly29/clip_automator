"""Artifact Writer (design spec §4.5).

Persists the captured snippet plus a metadata.json sidecar to the configured
output directory using a deterministic, sortable naming scheme:

    {output_dir}/{YYYYMMDD-HHMMSS}_{domain}_{video-id-or-index}/
        frame_000.png ... frame_00N.png   (frames mode)
        snippet.mp4                        (clip mode, if ffmpeg present)
        metadata.json                      (verdict + snapshots + config used)
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .capture import CaptureEngine
from .config import RunConfig, TargetConfig
from .logging_setup import get_logger
from .models import ArtifactRef, Frame, MediaSnapshot, Verdict

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(text: str, fallback: str = "x") -> str:
    s = _SAFE.sub("-", text).strip("-")
    return s[:64] or fallback


def domain_of(url: str) -> str:
    host = urlparse(url).hostname or "unknown"
    return _slug(host, "unknown")


def _video_id(target: TargetConfig) -> str:
    # Prefer a meaningful selector-derived id; fall back to the index.
    sel = target.video_selector
    if sel and sel != "video":
        return _slug(sel, f"idx{target.video_index}")
    return f"idx{target.video_index}"


class ArtifactWriter:
    def __init__(self, cfg: RunConfig):
        self._cfg = cfg
        self._log = get_logger()

    def dir_name(self, target: TargetConfig, when: datetime) -> str:
        ts = when.strftime("%Y%m%d-%H%M%S")
        return f"{ts}_{domain_of(target.url)}_{_video_id(target)}"

    def write(
        self,
        target: TargetConfig,
        frames: list[Frame],
        verdict: Verdict,
        before: MediaSnapshot | None,
        after: MediaSnapshot | None,
        capture_engine: CaptureEngine,
        recorded_video=None,
        selected_video: str | None = None,
        fullscreen: bool | None = None,
        ad_skipped: bool | None = None,
        clip_start_offset: float = 0.0,
        clip_duration: float | None = None,
    ) -> ArtifactRef:
        when = datetime.now(timezone.utc)
        out_dir = self._cfg.output_dir / self.dir_name(target, when)
        out_dir.mkdir(parents=True, exist_ok=True)

        files: list[str] = []
        # Always write frames (they are the primary, dependency-free evidence).
        for f in frames:
            name = f"frame_{f.index:03d}.png"
            (out_dir / name).write_bytes(f.png)
            files.append(name)

        if self._cfg.capture.mode == "clip":
            if recorded_video is not None:
                clip = capture_engine.finalize_clip(
                    recorded_video, out_dir,
                    start_offset_s=clip_start_offset, clip_duration_s=clip_duration)
                if clip is not None:
                    files.append(clip.name)
            else:
                self._log.warning("clip mode but no recording was captured; "
                                  "only frames saved")

        metadata = {
            "captured_at": when.isoformat(),
            "target": target.to_dict(),
            "selected_video": selected_video,
            "fullscreen": fullscreen,
            "ad_skipped": ad_skipped,
            # Captured media is video-only — Playwright recording and frame
            # screenshots never include audio (also why playback is muted).
            "audio_captured": False,
            "verdict": verdict.to_dict(),
            "before": before.to_dict() if before else None,
            "after": after.to_dict() if after else None,
            "frame_count": len(frames),
            "config": self._cfg.to_dict(),
        }
        meta_path = out_dir / "metadata.json"
        meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        files.append(meta_path.name)

        self._log.info("artifact written: %s (%d files)", out_dir, len(files))
        return ArtifactRef(
            directory=str(out_dir),
            files=files,
            metadata_path=str(meta_path),
        )
