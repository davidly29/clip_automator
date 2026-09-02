"""Tests for build_run_config (UI/JSON params -> RunConfig)."""

from __future__ import annotations

import pytest

from vpv.config import build_run_config
from vpv.errors import ConfigError


def test_requires_url(tmp_path):
    with pytest.raises(ConfigError):
        build_run_config({}, output_dir=tmp_path)


def test_rejects_non_http_url(tmp_path):
    with pytest.raises(ConfigError):
        build_run_config({"url": "ftp://x/y"}, output_dir=tmp_path)


def test_defaults_and_forced_fields(tmp_path):
    cfg = build_run_config({"url": "https://ex.com/v"}, output_dir=tmp_path)
    assert cfg.headless is True          # forced (no display in container)
    assert cfg.concurrency == 1          # forced (one at a time)
    assert cfg.capture.mode == "clip"
    assert cfg.capture.duration_s == 14.0
    assert cfg.targets[0].video_selector == "video"


def test_ids_fold_into_selectors(tmp_path):
    cfg = build_run_config(
        {"url": "https://ex.com/v", "play_id": "playbtn",
         "dismiss_ids": ["over18", "cookies"], "fullscreen_id": "fs"},
        output_dir=tmp_path)
    t = cfg.targets[0]
    assert t.play_selector == "#playbtn"
    assert t.fullscreen_selector == "#fs"
    assert t.dismiss_selectors == ("#over18", "#cookies")


def test_selector_takes_precedence_over_id(tmp_path):
    cfg = build_run_config(
        {"url": "https://ex.com/v", "play_selector": ".play", "play_id": "playbtn"},
        output_dir=tmp_path)
    assert cfg.targets[0].play_selector == ".play"


def test_viewport_and_extra_args(tmp_path):
    cfg = build_run_config(
        {"url": "https://ex.com/v", "viewport": "1280x720", "browser_args": "--foo"},
        output_dir=tmp_path, extra_browser_args=("--no-sandbox",))
    assert (cfg.viewport_width, cfg.viewport_height) == (1280, 720)
    assert cfg.browser_args == ("--foo", "--no-sandbox")


def test_bad_mode_rejected(tmp_path):
    with pytest.raises(ConfigError):
        build_run_config({"url": "https://ex.com/v", "mode": "movie"}, output_dir=tmp_path)


def test_search_query_needs_selector(tmp_path):
    # _validate enforces this cross-field rule.
    with pytest.raises(ConfigError):
        build_run_config({"url": "https://ex.com/v", "search_query": "cats"}, output_dir=tmp_path)
