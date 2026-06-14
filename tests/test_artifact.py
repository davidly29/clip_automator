"""Unit tests for the Artifact Writer and naming scheme (design spec §4.5)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from conftest import moving_frames, snapshot

from vpv.artifact import ArtifactWriter, domain_of
from vpv.capture import CaptureEngine
from vpv.config import CaptureConfig, RunConfig, TargetConfig
from vpv.models import FailureCode, Verdict


def _cfg(tmp_path) -> RunConfig:
    return RunConfig(
        targets=(TargetConfig(url="https://media.example.com/watch/123"),),
        output_dir=tmp_path,
        capture=CaptureConfig(mode="frames"),
    )


def test_domain_of():
    assert domain_of("https://media.example.com/watch/123") == "media.example.com"
    assert domain_of("not a url") == "unknown"


def test_dir_name_is_structured(tmp_path):
    cfg = _cfg(tmp_path)
    w = ArtifactWriter(cfg)
    when = datetime(2026, 6, 14, 9, 30, 0, tzinfo=timezone.utc)
    name = w.dir_name(cfg.targets[0], when)
    assert name == "20260614-093000_media.example.com_idx0"


def test_write_produces_frames_and_metadata(tmp_path):
    cfg = _cfg(tmp_path)
    w = ArtifactWriter(cfg)
    frames = moving_frames(3)
    verdict = Verdict(passed=True, code=FailureCode.PASS, reasons=["ok"], signals={"m": 1})
    ref = w.write(cfg.targets[0], frames, verdict, snapshot(0.0), snapshot(3.0),
                  CaptureEngine(cfg.capture))

    out = list(tmp_path.iterdir())
    assert len(out) == 1
    files = {p.name for p in out[0].iterdir()}
    assert "frame_000.png" in files
    assert "frame_002.png" in files
    assert "metadata.json" in files

    meta = json.loads((out[0] / "metadata.json").read_text())
    assert meta["verdict"]["passed"] is True
    assert meta["frame_count"] == 3
    assert meta["target"]["url"] == "https://media.example.com/watch/123"
    assert ref.metadata_path.endswith("metadata.json")
