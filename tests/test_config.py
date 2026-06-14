"""Unit tests for the Config Loader (design spec §8: contract tests)."""

from __future__ import annotations

import json

import pytest

from vpv.config import load_config
from vpv.errors import ConfigError


def test_url_shorthand(tmp_path):
    cfg, rf = load_config(["--url", "https://example.com/v", "--output-dir", str(tmp_path)])
    assert len(cfg.targets) == 1
    assert cfg.targets[0].url == "https://example.com/v"
    assert cfg.output_dir == tmp_path
    assert rf is None


def test_cli_overrides_file(tmp_path):
    cfg_file = tmp_path / "c.json"
    cfg_file.write_text(json.dumps({
        "output_dir": str(tmp_path),
        "concurrency": 4,
        "capture": {"mode": "frames", "duration_s": 2.0},
        "targets": [{"url": "https://a.test/1"}],
    }))
    cfg, _ = load_config([
        "--config", str(cfg_file), "--concurrency", "1", "--duration", "9",
    ])
    assert cfg.concurrency == 1            # CLI wins
    assert cfg.capture.duration_s == 9.0   # CLI wins
    assert cfg.targets[0].url == "https://a.test/1"


def test_rejects_unknown_top_key(tmp_path):
    cfg_file = tmp_path / "c.json"
    cfg_file.write_text(json.dumps({"output_dir": str(tmp_path), "bogus": 1,
                                    "targets": [{"url": "https://a.test"}]}))
    with pytest.raises(ConfigError, match="Unknown config key"):
        load_config(["--config", str(cfg_file)])


def test_rejects_unknown_target_key(tmp_path):
    cfg_file = tmp_path / "c.json"
    cfg_file.write_text(json.dumps({"output_dir": str(tmp_path),
                                    "targets": [{"url": "https://a.test", "nope": 1}]}))
    with pytest.raises(ConfigError, match="targets\\[0\\]"):
        load_config(["--config", str(cfg_file)])


def test_requires_targets(tmp_path):
    with pytest.raises(ConfigError, match="No targets"):
        load_config(["--output-dir", str(tmp_path)])


def test_rejects_non_http_url(tmp_path):
    with pytest.raises(ConfigError, match="http"):
        load_config(["--url", "ftp://x.test/v", "--output-dir", str(tmp_path)])


def test_rejects_bad_duration(tmp_path):
    with pytest.raises(ConfigError, match="duration"):
        load_config(["--url", "https://a.test", "--output-dir", str(tmp_path),
                     "--duration", "0"])


def test_rejects_unwritable_output_dir(tmp_path):
    # A directory path nested *under a regular file* cannot be created on any OS.
    blocker = tmp_path / "iam_a_file"
    blocker.write_text("x")
    bad_dir = blocker / "subdir"
    with pytest.raises(ConfigError, match="writable|not writable|output_dir"):
        load_config(["--url", "https://a.test", "--output-dir", str(bad_dir)])


def test_jsonc_comments_allowed(tmp_path):
    cfg_file = tmp_path / "c.jsonc"
    cfg_file.write_text(
        "{\n"
        '  // a comment\n'
        f'  "output_dir": {json.dumps(str(tmp_path))},\n'
        '  "targets": [{"url": "https://a.test"}]  /* inline */\n'
        "}\n"
    )
    cfg, _ = load_config(["--config", str(cfg_file)])
    assert cfg.targets[0].url == "https://a.test"


def test_search_id_becomes_css_id(tmp_path):
    cfg, _ = load_config([
        "--url", "https://a.test", "--output-dir", str(tmp_path),
        "--search-id", "searchbar", "--search-query", "cats",
    ])
    t = cfg.targets[0]
    assert t.search_selector == "#searchbar"
    assert t.search_query == "cats"


def test_search_selector_takes_precedence_over_id(tmp_path):
    cfg, _ = load_config([
        "--url", "https://a.test", "--output-dir", str(tmp_path),
        "--search-selector", "input[name=q]", "--search-id", "ignored",
        "--search-query", "x",
    ])
    assert cfg.targets[0].search_selector == "input[name=q]"


def test_interaction_fields_from_file(tmp_path):
    cfg_file = tmp_path / "c.json"
    cfg_file.write_text(json.dumps({
        "output_dir": str(tmp_path),
        "targets": [{
            "url": "https://a.test",
            "video_selector": "#player",
            "search_id": "search",
            "search_query": "dogs",
            "result_selector": "#result-0",
            "play_selector": "#play",
            "fullscreen_selector": "#fs",
        }],
    }))
    cfg, _ = load_config(["--config", str(cfg_file)])
    t = cfg.targets[0]
    assert t.search_selector == "#search"
    assert t.result_selector == "#result-0"
    assert t.play_selector == "#play"
    assert t.fullscreen_selector == "#fs"


def test_search_query_without_selector_rejected(tmp_path):
    with pytest.raises(ConfigError, match="search_query requires"):
        load_config(["--url", "https://a.test", "--output-dir", str(tmp_path),
                     "--search-query", "cats"])


def test_dismiss_id_and_random_ids_cli(tmp_path):
    cfg, _ = load_config([
        "--url", "https://a.test", "--output-dir", str(tmp_path),
        "--dismiss-id", "accept",
        "--random-id", "v1", "--random-id", "v2", "--random-seed", "42",
    ])
    t = cfg.targets[0]
    assert t.dismiss_selector == "#accept"
    assert t.random_ids == ("v1", "v2")
    assert cfg.random_seed == 42
    assert cfg.auto_dismiss_consent is True  # default on


def test_no_auto_dismiss_flag(tmp_path):
    cfg, _ = load_config(["--url", "https://a.test", "--output-dir", str(tmp_path),
                          "--no-auto-dismiss"])
    assert cfg.auto_dismiss_consent is False


def test_fullscreen_flag(tmp_path):
    on, _ = load_config(["--url", "https://a.test", "--output-dir", str(tmp_path)])
    assert on.fullscreen is True  # default on
    off, _ = load_config(["--url", "https://a.test", "--output-dir", str(tmp_path),
                          "--no-fullscreen"])
    assert off.fullscreen is False


def test_fullscreen_target_id(tmp_path):
    cfg, _ = load_config(["--url", "https://a.test", "--output-dir", str(tmp_path),
                          "--fullscreen-id", "player-full-bleed-container"])
    assert cfg.targets[0].fullscreen_target == "#player-full-bleed-container"


def test_fullscreen_target_selector_wins_over_id(tmp_path):
    cfg, _ = load_config(["--url", "https://a.test", "--output-dir", str(tmp_path),
                          "--fullscreen-target", "#movie_player",
                          "--fullscreen-id", "ignored"])
    assert cfg.targets[0].fullscreen_target == "#movie_player"


def test_consent_texts_cli(tmp_path):
    cfg, _ = load_config(["--url", "https://a.test", "--output-dir", str(tmp_path),
                          "--consent-text", "Enter site", "--consent-text", "Proceed"])
    assert cfg.consent_texts == ("Enter site", "Proceed")


def test_consent_texts_from_file(tmp_path):
    cfg_file = tmp_path / "c.json"
    cfg_file.write_text(json.dumps({
        "output_dir": str(tmp_path),
        "consent_texts": ["Accept all", "Zustimmen"],
        "targets": [{"url": "https://a.test"}],
    }))
    cfg, _ = load_config(["--config", str(cfg_file)])
    assert cfg.consent_texts == ("Accept all", "Zustimmen")


def test_consent_texts_must_be_list(tmp_path):
    cfg_file = tmp_path / "c.json"
    cfg_file.write_text(json.dumps({
        "output_dir": str(tmp_path),
        "consent_texts": "Accept",
        "targets": [{"url": "https://a.test"}],
    }))
    with pytest.raises(ConfigError, match="consent_texts must be a list"):
        load_config(["--config", str(cfg_file)])


def test_random_ids_from_file(tmp_path):
    cfg_file = tmp_path / "c.json"
    cfg_file.write_text(json.dumps({
        "output_dir": str(tmp_path),
        "auto_dismiss_consent": False,
        "targets": [{"url": "https://a.test", "dismiss_id": "ok",
                     "random_ids": ["a", "b", "c"]}],
    }))
    cfg, _ = load_config(["--config", str(cfg_file)])
    assert cfg.auto_dismiss_consent is False
    assert cfg.targets[0].dismiss_selector == "#ok"
    assert cfg.targets[0].random_ids == ("a", "b", "c")


def test_random_ids_must_be_list(tmp_path):
    cfg_file = tmp_path / "c.json"
    cfg_file.write_text(json.dumps({
        "output_dir": str(tmp_path),
        "targets": [{"url": "https://a.test", "random_ids": "v1"}],
    }))
    with pytest.raises(ConfigError, match="random_ids must be a list"):
        load_config(["--config", str(cfg_file)])


def test_default_frame_count_is_two(tmp_path):
    cfg, _ = load_config(["--url", "https://a.test", "--output-dir", str(tmp_path),
                          "--duration", "4", "--interval-ms", "500"])
    # Defaults to exactly 2 screenshots regardless of duration/interval.
    assert cfg.capture.frame_count == 2
    assert cfg.capture.planned_frame_count() == 2


def test_warmup_default_and_override(tmp_path):
    base, _ = load_config(["--url", "https://a.test", "--output-dir", str(tmp_path)])
    assert base.capture.warmup_s == 0.0
    cfg, _ = load_config(["--url", "https://a.test", "--output-dir", str(tmp_path),
                          "--warmup", "2.5"])
    assert cfg.capture.warmup_s == 2.5


def test_frame_count_cli_override(tmp_path):
    cfg, _ = load_config(["--url", "https://a.test", "--output-dir", str(tmp_path),
                          "--frame-count", "5"])
    assert cfg.capture.planned_frame_count() == 5


def test_frame_count_null_enables_interval_sampling(tmp_path):
    cfg_file = tmp_path / "c.json"
    cfg_file.write_text(json.dumps({
        "output_dir": str(tmp_path),
        "capture": {"frame_count": None, "duration_s": 4, "interval_ms": 500},
        "targets": [{"url": "https://a.test"}],
    }))
    cfg, _ = load_config(["--config", str(cfg_file)])
    assert cfg.capture.frame_count is None
    # 4000ms / 500ms + 1 = 9 frames.
    assert cfg.capture.planned_frame_count() == 9
