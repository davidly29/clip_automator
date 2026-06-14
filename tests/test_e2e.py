"""End-to-end tests against local fixture pages (design spec §8).

These drive the full pipeline through a real headless browser. They are skipped
automatically if Playwright or its browser binaries are not installed, so the
pure-logic suite still runs everywhere.

Run the browsers once with:  python -m playwright install chromium
"""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

from vpv.config import RunConfig, TargetConfig, CaptureConfig
from vpv.models import FailureCode
from vpv.orchestrator import Orchestrator

FIXTURES = Path(__file__).parent / "fixtures"


def _browser_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            b.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _browser_available(),
    reason="Playwright chromium not installed (run: python -m playwright install chromium)",
)


@pytest.fixture(scope="module")
def server():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(FIXTURES))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def _run(server, page, tmp_path, **target_kw):
    cfg = RunConfig(
        targets=(TargetConfig(url=f"{server}/{page}", **target_kw),),
        output_dir=tmp_path,
        capture=CaptureConfig(mode="frames", duration_s=2.0, interval_ms=300),
        nav_timeout_s=15.0,
        play_confirm_timeout_s=8.0,
        retries=0,
        concurrency=1,
    )
    import asyncio
    return asyncio.run(Orchestrator(cfg).run())


def test_playing_passes(server, tmp_path):
    run = _run(server, "playing.html", tmp_path)
    r = run.results[0]
    assert r.passed is True, r.reasons
    assert r.code is FailureCode.PASS
    assert run.exit_code == 0
    # Artifact written.
    assert r.artifact is not None
    assert Path(r.artifact.metadata_path).exists()


def test_default_captures_two_frames(server, tmp_path):
    run = _run(server, "playing.html", tmp_path)
    r = run.results[0]
    pngs = [f for f in r.artifact.files if f.endswith(".png")]
    assert len(pngs) == 2, pngs


def test_frozen_fails(server, tmp_path):
    run = _run(server, "frozen.html", tmp_path)
    r = run.results[0]
    assert r.passed is False
    assert r.code is FailureCode.FROZEN_FRAMES
    assert run.exit_code == 1


def test_broken_source_fails(server, tmp_path):
    run = _run(server, "broken.html", tmp_path)
    r = run.results[0]
    assert r.passed is False
    assert r.code in {FailureCode.MEDIA_ERROR, FailureCode.NO_PLAYBACK_PROGRESS}


def test_video_not_found(server, tmp_path):
    run = _run(server, "missing.html", tmp_path)
    r = run.results[0]
    assert r.passed is False
    assert r.code is FailureCode.VIDEO_NOT_FOUND


def test_search_driven_flow_passes(server, tmp_path):
    """Full parameterized flow: search -> open result -> play -> fullscreen."""
    run = _run(
        server, "search_site.html", tmp_path,
        video_selector="#player",
        search_selector="#search",
        search_query="cats",
        search_submit="#go",
        result_selector="#result-0",
        play_selector="#play",
        fullscreen_selector="#fs",
    )
    r = run.results[0]
    assert r.passed is True, r.reasons
    assert r.code is FailureCode.PASS
    assert run.exit_code == 0


def test_consent_dismiss_and_random_pick(server, tmp_path):
    """Auto-dismiss the consent modal, then randomly open one of the videos."""
    run = _run(
        server, "homepage.html", tmp_path,
        video_selector="#player",
        random_ids=("v1", "v2", "v3"),
    )
    r = run.results[0]
    assert r.passed is True, r.reasons
    assert r.selected_video in {"#v1", "#v2", "#v3"}


def test_explicit_dismiss_id_with_auto_off(server, tmp_path):
    """Explicit OK-button selector works even with the heuristic disabled."""
    import asyncio
    cfg = RunConfig(
        targets=(TargetConfig(
            url=f"{server}/homepage.html",
            video_selector="#player",
            dismiss_selectors=("#accept",),
            random_ids=("v2",),
        ),),
        output_dir=tmp_path,
        capture=CaptureConfig(mode="frames", duration_s=1.5, interval_ms=300),
        nav_timeout_s=15.0, play_confirm_timeout_s=8.0, retries=0, concurrency=1,
        auto_dismiss_consent=False,
        random_seed=7,
    )
    run = asyncio.run(Orchestrator(cfg).run())
    r = run.results[0]
    assert r.passed is True, r.reasons
    assert r.selected_video == "#v2"


def test_random_ids_none_present_fails(server, tmp_path):
    run = _run(server, "homepage.html", tmp_path,
               video_selector="#player", random_ids=("nope1", "nope2"))
    r = run.results[0]
    assert r.passed is False
    assert r.code is FailureCode.VIDEO_NOT_FOUND


def test_consent_dismissal_that_navigates(server, tmp_path):
    """A consent click that redirects must not crash the run; flow continues."""
    run = _run(server, "consent_redirect.html", tmp_path, video_selector="video")
    # The run completes with a real per-target result (never a tool_error/exit 2).
    assert run.tool_error is None
    assert run.exit_code in (0, 1)
    r = run.results[0]
    # After settling on the redirected page, the video is found and plays.
    assert r.passed is True, r.reasons


def test_custom_consent_text_required(server, tmp_path):
    """A non-standard entry button is only cleared when its phrase is configured."""
    import asyncio

    def run_with(consent_texts):
        cfg = RunConfig(
            targets=(TargetConfig(url=f"{server}/custom_consent.html",
                                  video_selector="#player", random_ids=("v1",)),),
            output_dir=tmp_path,
            capture=CaptureConfig(mode="frames", duration_s=1.5, interval_ms=300),
            nav_timeout_s=15.0, play_confirm_timeout_s=6.0, retries=0, concurrency=1,
            consent_texts=consent_texts,
        )
        return asyncio.run(Orchestrator(cfg).run()).results[0]

    # Built-in phrases don't match "Enter site": the overlay blocks the click.
    blocked = run_with(())
    assert blocked.passed is False

    # Supplying the phrase clears the overlay and the video plays.
    ok = run_with(("Enter site",))
    assert ok.passed is True, ok.reasons


def test_fullscreen_is_entered(server, tmp_path):
    """With fullscreen on, the video element is actually put into fullscreen."""
    import asyncio
    from vpv.browser import BrowserController

    target = TargetConfig(
        url=f"{server}/search_site.html", video_selector="#player",
        search_selector="#search", search_query="x", search_submit="#go",
        result_selector="#result-0", play_selector="#play",
    )
    cfg = RunConfig(
        targets=(target,), output_dir=tmp_path,
        capture=CaptureConfig(mode="frames", duration_s=1.0, interval_ms=300),
        nav_timeout_s=15.0, play_confirm_timeout_s=8.0, retries=0, concurrency=1,
        fullscreen=True,
    )

    async def run():
        ctrl = BrowserController(cfg)
        await ctrl.open()
        try:
            context, page = await ctrl.new_page()
            await ctrl.goto(page, target.url)
            await ctrl.run_interaction(page, target)
            video = await ctrl.locate_video(page, target)
            await ctrl.click_play(page, target)
            await ctrl.go_fullscreen(page, video, target)
            in_fs = await page.evaluate("() => !!document.fullscreenElement")
            await context.close()
            return in_fs
        finally:
            await ctrl.close()

    assert asyncio.run(run()) is True


def test_fullscreen_target_container(server, tmp_path):
    """--fullscreen-target makes a wrapping container (not the video) fullscreen."""
    import asyncio
    from vpv.browser import BrowserController

    target = TargetConfig(
        url=f"{server}/container_fs.html", video_selector="#player",
        play_selector="#play", fullscreen_target="#wrap",
    )
    cfg = RunConfig(
        targets=(target,), output_dir=tmp_path,
        capture=CaptureConfig(mode="frames", duration_s=1.0),
        nav_timeout_s=15.0, play_confirm_timeout_s=8.0, retries=0, concurrency=1,
        fullscreen=True,
    )

    async def run():
        ctrl = BrowserController(cfg)
        await ctrl.open()
        try:
            context, page = await ctrl.new_page()
            await ctrl.goto(page, target.url)
            video = await ctrl.locate_video(page, target)
            await ctrl.click_play(page, target)
            ok = await ctrl.go_fullscreen(page, video, target)
            fs_id = await page.evaluate(
                "() => document.fullscreenElement ? document.fullscreenElement.id : null")
            await context.close()
            return ok, fs_id
        finally:
            await ctrl.close()

    ok, fs_id = asyncio.run(run())
    assert ok is True
    assert fs_id == "wrap"


def test_age_gate_random_selector_and_skip_ad(server, tmp_path):
    """Full adult-site flow: dismiss two <span> gates, randomly open a video_*
    div, skip the pre-roll ad, then verify the content plays."""
    import asyncio
    target = TargetConfig(
        url=f"{server}/age_gated_site.html",
        dismiss_selectors=("#disclaimer-over18btn", "#disclaimer-accept_cookies"),
        random_selector="div[id^=video_]",
        skip_ad_selector="#skip",
        video_selector="#player",
    )
    cfg = RunConfig(
        targets=(target,), output_dir=tmp_path,
        capture=CaptureConfig(mode="frames", duration_s=1.0),
        nav_timeout_s=15.0, play_confirm_timeout_s=8.0, retries=0, concurrency=1,
        ad_timeout_s=8.0, random_seed=1,
    )
    run = asyncio.run(Orchestrator(cfg).run())
    r = run.results[0]
    assert r.passed is True, r.reasons
    assert r.selected_video.startswith("div[id^=video_]")
    assert r.ad_skipped is True


def test_play_then_ad_then_skip(server, tmp_path):
    """The ad only appears after clicking play; VPV must play, then skip, then
    capture the real content."""
    run = _run(
        server, "play_then_ad.html", tmp_path,
        random_selector="div[id^=video_]",
        play_selector="#play",
        skip_ad_selector="#skip",
        video_selector="#player",
    )
    r = run.results[0]
    assert r.passed is True, r.reasons
    assert r.ad_skipped is True


def test_skip_ad_via_text_selector(server, tmp_path):
    """The 'text=Skip ad' Playwright selector also finds the skip control."""
    run = _run(
        server, "age_gated_site.html", tmp_path,
        dismiss_selectors=("#disclaimer-over18btn", "#disclaimer-accept_cookies"),
        random_selector="div[id^=video_]",
        skip_ad_selector="text=Skip ad",
        video_selector="#player",
    )
    r = run.results[0]
    assert r.passed is True, r.reasons
    assert r.ad_skipped is True


def test_clip_mode_produces_real_video(server, tmp_path):
    """clip mode records an actual playable video file via Playwright + ffmpeg."""
    import asyncio
    cfg = RunConfig(
        targets=(TargetConfig(url=f"{server}/playing.html"),),
        output_dir=tmp_path,
        capture=CaptureConfig(mode="clip", duration_s=2.0, interval_ms=300),
        nav_timeout_s=15.0, play_confirm_timeout_s=8.0, retries=0, concurrency=1,
    )
    run = asyncio.run(Orchestrator(cfg).run())
    r = run.results[0]
    assert r.passed is True, r.reasons
    clips = [f for f in r.artifact.files if f.startswith("snippet.")]
    assert clips, "expected a snippet.* clip artifact"
    clip_path = Path(r.artifact.directory) / clips[0]
    assert clip_path.exists() and clip_path.stat().st_size > 0
