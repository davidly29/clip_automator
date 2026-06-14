"""Browser Controller (design spec §4.2).

Wraps Playwright: launches a headless browser, navigates, resolves the target
video element, triggers/confirms playback, and reads HTMLMediaElement state via
in-page JS evaluation.

Playwright is imported lazily so the pure-logic modules (config, verify) can be
imported and unit-tested without the browser dependency installed.
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from dataclasses import dataclass

from .config import RunConfig, TargetConfig
from .errors import (
    AutoplayBlocked,
    BrowserError,
    MediaErrorDetected,
    NavTimeout,
    NotAMediaElement,
    ProtectedContent,
    VideoNotFound,
)
from .logging_setup import get_logger
from .models import MediaSnapshot

# JS evaluated against the resolved HTMLVideoElement to read its state.
_READ_STATE_JS = """
el => ({
  current_time: el.currentTime,
  duration: (isFinite(el.duration) ? el.duration : null),
  paused: el.paused,
  ended: el.ended,
  ready_state: el.readyState,
  network_state: el.networkState,
  error_code: (el.error ? el.error.code : null),
  video_width: el.videoWidth,
  video_height: el.videoHeight,
  has_media_keys: !!(el.mediaKeys || window.__vpvEncrypted),
  tag: el.tagName
})
"""

_REQUEST_FS_JS = """
async el => {
  try {
    const fn = el.requestFullscreen || el.webkitRequestFullscreen
            || el.mozRequestFullScreen || el.msRequestFullscreen;
    if (fn) { const r = fn.call(el); if (r && typeof r.then === 'function') await r; }
  } catch (e) { /* needs a user gesture / may be denied in headless */ }
  return !!(document.fullscreenElement || document.webkitFullscreenElement);
}
"""

_TRY_PLAY_JS = """
async el => {
  el.muted = true;          // satisfy autoplay policies
  el.volume = 0;
  try {
    const p = el.play();
    if (p && typeof p.then === 'function') { await p; }
    return { ok: true, name: null, error: null, error_code: (el.error ? el.error.code : null) };
  } catch (e) {
    return {
      ok: false,
      name: (e && e.name) ? e.name : 'Error',
      error: (e && e.name) ? (e.name + ': ' + e.message) : String(e),
      error_code: (el.error ? el.error.code : null)
    };
  }
}
"""


# Heuristic consent/first-visit dismissal. Matched against the *accessible name*
# of buttons; kept deliberately conservative to avoid clicking the wrong control.
# Operators can override this list via RunConfig.consent_texts.
_DEFAULT_CONSENT_PHRASES = (
    "accept all", "accept cookies", "accept & close", "accept",
    "agree", "i agree", "i accept", "got it", "ok", "okay",
    "allow all", "allow", "consent", "yes i agree",
)


def _consent_pattern(phrases) -> "re.Pattern[str]":
    """Build an anchored, case-insensitive alternation over the given phrases."""
    items = [re.escape(p.strip()) for p in phrases if p and p.strip()]
    if not items:
        items = [re.escape(p) for p in _DEFAULT_CONSENT_PHRASES]
    return re.compile(r"^\s*(" + "|".join(items) + r")\s*$", re.IGNORECASE)

# Well-known consent-framework button selectors (checked before the text scan).
_CONSENT_SELECTORS = (
    "#onetrust-accept-btn-handler",
    "#accept-recommended-btn-handler",
    "button.fc-cta-consent",                 # Google Funding Choices
    "button[aria-label*='accept' i]",
    "button[title*='accept' i]",
    "[data-testid*='accept' i]",
)


@dataclass
class VideoHandle:
    """A resolved video element plus its source target config."""

    locator: object        # playwright.async_api.Locator
    target: TargetConfig


def _snapshot_from_dict(d: dict) -> MediaSnapshot:
    return MediaSnapshot(
        current_time=float(d["current_time"]),
        duration=(float(d["duration"]) if d["duration"] is not None else None),
        paused=bool(d["paused"]),
        ended=bool(d["ended"]),
        ready_state=int(d["ready_state"]),
        network_state=int(d["network_state"]),
        error_code=(int(d["error_code"]) if d["error_code"] is not None else None),
        video_width=int(d["video_width"]),
        video_height=int(d["video_height"]),
        has_media_keys=bool(d.get("has_media_keys", False)),
    )


class BrowserController:
    """Owns a single Playwright browser; one context/page per target."""

    def __init__(self, cfg: RunConfig):
        self._cfg = cfg
        self._pw = None
        self._browser = None
        self._log = get_logger()
        self._consent_re = _consent_pattern(cfg.consent_texts)

    async def open(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:  # pragma: no cover - environment dependent
            raise BrowserError(
                "Playwright is not installed. Run: pip install playwright && playwright install"
            ) from e
        try:
            self._pw = await async_playwright().start()
            launcher = getattr(self._pw, self._cfg.browser)
            launch_kwargs: dict = {"headless": self._cfg.headless}
            if self._cfg.browser_args:
                launch_kwargs["args"] = list(self._cfg.browser_args)
                self._log.info("browser launch args: %s", " ".join(self._cfg.browser_args))
            self._browser = await launcher.launch(**launch_kwargs)
        except Exception as e:
            raise BrowserError(f"failed to launch {self._cfg.browser}: {e}") from e

    async def close(self) -> None:
        try:
            if self._browser:
                await self._browser.close()
        finally:
            if self._pw:
                await self._pw.stop()
            self._browser = None
            self._pw = None

    async def new_page(self, record_video_dir: str | None = None):
        """Create an isolated context+page for one target (concurrency-safe).

        When ``record_video_dir`` is given, Playwright records the whole session
        to a .webm in that directory (flushed when the page/context closes); this
        backs clip mode's "actual video" artifact.
        """
        if not self._browser:
            raise BrowserError("browser not open")
        size = {"width": self._cfg.viewport_width, "height": self._cfg.viewport_height}
        ctx_kwargs: dict = {"viewport": dict(size)}
        if self._cfg.user_agent:
            ctx_kwargs["user_agent"] = self._cfg.user_agent
        if record_video_dir:
            ctx_kwargs["record_video_dir"] = record_video_dir
            # Record the clip at the viewport size (e.g. 1920x1080).
            ctx_kwargs["record_video_size"] = dict(size)
        context = await self._browser.new_context(**ctx_kwargs)
        page = await context.new_page()
        # Flag EME/DRM early: a capture-phase listener catches the 'encrypted'
        # event for any media element (it fires before mediaKeys is observable,
        # and on sites where el.mediaKeys never becomes set).
        await page.add_init_script(
            "document.addEventListener('encrypted',"
            "()=>{window.__vpvEncrypted=true;},true);")
        return context, page

    async def dismiss_overlays(self, page, target: TargetConfig) -> list[str]:
        """Best-effort dismissal of consent / age-gate / cookie overlays.

        Strategy:
          1. Click every explicit ``dismiss_selectors`` element the operator gave
             (e.g. an age gate then a cookie banner), in order — these can be any
             element (a <span>, link, ...), not just buttons.
          2. Only if none were given and ``auto_dismiss_consent`` is on, fall back
             to known consent selectors and the accept-button text heuristic.
        Never raises — a page without an overlay is the normal case.
        """
        clicked: list[str] = []

        # 1) explicit, operator-provided elements (age gate, cookies, ...).
        for sel in target.dismiss_selectors:
            if await self._click_selector(page, sel, timeout_ms=5000):
                clicked.append(sel)
                # Each dismissal may reveal the next overlay or reload the page.
                await self._settle(page)
        if clicked:
            self._log.info("dismissed overlay(s) via %s", ", ".join(clicked))
            return clicked

        if not self._cfg.auto_dismiss_consent:
            return []

        # 2a) known consent-framework selectors (OneTrust, Funding Choices, ...).
        for sel in _CONSENT_SELECTORS:
            for frame in page.frames:
                if await self._try_click(frame.locator(sel).first):
                    self._log.info("auto-dismissed consent via %s", sel)
                    await self._settle(page)
                    return [sel]

        # 2b) role+text heuristic across all frames.
        for frame in page.frames:
            try:
                btn = frame.get_by_role("button", name=self._consent_re).first
            except Exception:  # noqa: BLE001 - detached/cross-origin frame
                continue
            if await self._try_click(btn):
                self._log.info("auto-dismissed consent via accept-button text")
                await self._settle(page)
                return ["role=button[accept]"]
        return []

    async def _try_click(self, locator, timeout_ms: int = 1200) -> bool:
        from playwright.async_api import Error as PWError
        from playwright.async_api import TimeoutError as PWTimeout
        try:
            if await locator.count() == 0:
                return False
            await locator.click(timeout=timeout_ms)
            return True
        except (PWTimeout, PWError):
            return False

    async def _click_selector(self, page, selector: str, timeout_ms: int) -> bool:
        """Wait for a selector to become visible, then click it. Best-effort."""
        from playwright.async_api import Error as PWError
        from playwright.async_api import TimeoutError as PWTimeout
        loc = page.locator(selector).first
        try:
            await loc.wait_for(state="visible", timeout=timeout_ms)
            await loc.click(timeout=timeout_ms)
            return True
        except (PWTimeout, PWError):
            return False

    async def pick_random_video(
        self, page, target: TargetConfig, rng: random.Random
    ) -> str | None:
        """Open a random video. Prefers ``random_selector`` (a CSS selector with
        many matches), else falls back to ``random_ids`` (explicit ids)."""
        from playwright.async_api import Error as PWError
        from playwright.async_api import TimeoutError as PWTimeout

        chosen: str
        chosen_loc = None

        if target.random_selector:
            loc = page.locator(target.random_selector)
            try:
                count = await loc.count()
            except Exception:  # noqa: BLE001 - bad selector
                count = 0
            if count == 0:
                raise VideoNotFound(
                    f"no elements match random selector {target.random_selector!r}")
            idx = rng.randrange(count)
            chosen_loc = loc.nth(idx)
            chosen = f"{target.random_selector}[{idx}]"
            self._log.info("randomly selected #%d of %d for %s",
                           idx, count, target.random_selector)
        elif target.random_ids:
            present: list[str] = []
            for rid in target.random_ids:
                sel = rid if rid[:1] in "#.[" else f"#{rid}"
                try:
                    if await page.locator(sel).count() > 0:
                        present.append(sel)
                except Exception:  # noqa: BLE001 - bad selector => skip candidate
                    continue
            if not present:
                raise VideoNotFound(
                    f"none of the random video ids were found: {list(target.random_ids)}")
            chosen = rng.choice(present)
            chosen_loc = page.locator(chosen).first
            self._log.info("randomly selected %s (from %d present)", chosen, len(present))
        else:
            return None

        try:
            await chosen_loc.click(timeout=self._cfg.play_confirm_timeout_s * 1000)
        except (PWTimeout, PWError) as e:
            raise BrowserError(f"could not open random video {chosen!r}: {e}") from e
        await self._settle(page)
        return chosen

    async def skip_ad(self, page, target: TargetConfig) -> bool:
        """Skip a pre-roll ad by clicking the skip control once it appears.

        Pre-roll ads typically enforce a few seconds before the skip control
        becomes clickable; Playwright's wait handles that — we just wait up to
        ``ad_timeout_s`` for it to be visible, then click. Best-effort.
        """
        if not target.skip_ad_selector:
            return False
        from playwright.async_api import Error as PWError
        from playwright.async_api import TimeoutError as PWTimeout
        loc = page.locator(target.skip_ad_selector).first
        try:
            await loc.wait_for(state="visible", timeout=self._cfg.ad_timeout_s * 1000)
            await loc.click(timeout=5000)
        except (PWTimeout, PWError):
            self._log.debug("no skippable ad via %s within %.0fs",
                            target.skip_ad_selector, self._cfg.ad_timeout_s)
            return False
        self._log.info("skipped pre-roll ad via %s", target.skip_ad_selector)
        await self._settle(page)
        return True

    async def run_interaction(self, page, target: TargetConfig) -> None:
        """Drive the optional search -> open-result flow before locating video."""
        from playwright.async_api import Error as PWError
        from playwright.async_api import TimeoutError as PWTimeout

        timeout_ms = self._cfg.play_confirm_timeout_s * 1000

        # 1) Type into the search bar and submit.
        if target.search_selector and target.search_query is not None:
            box = page.locator(target.search_selector).first
            try:
                await box.wait_for(state="visible", timeout=timeout_ms)
                await box.click()
                await box.fill(target.search_query)
                self._log.info("searched %r via %s", target.search_query,
                               target.search_selector)
                if target.search_submit:
                    await page.locator(target.search_submit).first.click(timeout=timeout_ms)
                else:
                    await box.press("Enter")
            except (PWTimeout, PWError) as e:
                raise BrowserError(f"search interaction failed: {e}") from e
            await self._settle(page)

        # 2) Click a search result to open the video.
        if target.result_selector:
            try:
                result = page.locator(target.result_selector).first
                await result.wait_for(state="visible", timeout=timeout_ms)
                await result.click(timeout=timeout_ms)
                self._log.info("opened result %s", target.result_selector)
            except (PWTimeout, PWError) as e:
                raise BrowserError(f"could not open result {target.result_selector!r}: {e}") from e
            await self._settle(page)

    async def _settle(self, page) -> None:
        from playwright.async_api import TimeoutError as PWTimeout
        try:
            await page.wait_for_load_state("networkidle",
                                           timeout=self._cfg.nav_timeout_s * 1000)
        except PWTimeout:
            self._log.debug("networkidle not reached after interaction; continuing")

    async def click_play(self, page, target: TargetConfig) -> bool:
        """Click the play button (page-level) to start playback.

        On many sites this is what triggers a pre-roll ad, so it must happen
        *before* :meth:`skip_ad`. No-op (returns False) if no play selector is
        configured — :meth:`try_play` is the programmatic fallback. Best-effort.
        """
        if not target.play_selector:
            return False
        ok = await self._click_selector(
            page, target.play_selector, timeout_ms=self._cfg.play_confirm_timeout_s * 1000)
        if ok:
            self._log.info("clicked play control (%s)", target.play_selector)
        else:
            self._log.debug("play control %r not actionable", target.play_selector)
        return ok

    async def go_fullscreen(self, page, video: "VideoHandle", target: TargetConfig) -> bool:
        """Put the target element into fullscreen before capture (best-effort).

        Clicks a site fullscreen button if configured, otherwise requests the
        Fullscreen API on ``fullscreen_target`` (or the video). Returns whether
        the page ended up in fullscreen. Does NOT click play (see click_play).
        """
        if not self._cfg.fullscreen:
            return False

        from playwright.async_api import Error as PWError
        from playwright.async_api import TimeoutError as PWTimeout

        clicked_fs = False
        if target.fullscreen_selector:
            try:
                ctrl = page.locator(target.fullscreen_selector).first
                await ctrl.wait_for(state="visible", timeout=5000)
                await ctrl.click(timeout=5000)
                clicked_fs = True
                self._log.info("clicked fullscreen control (%s)", target.fullscreen_selector)
            except (PWTimeout, PWError) as e:
                self._log.debug("fullscreen control %r not actionable: %s",
                                target.fullscreen_selector, e)

        # If a site control already entered fullscreen on the right element,
        # respect it — don't override with a programmatic request elsewhere.
        try:
            if await page.evaluate("() => !!document.fullscreenElement"):
                self._log.info("already fullscreen via site control")
                return True
        except Exception:  # noqa: BLE001 - fall through to programmatic request
            pass
        return await self._ensure_fullscreen(page, video, target, had_gesture=clicked_fs)

    async def _request_fs(self, fs_locator) -> bool:
        try:
            return bool(await fs_locator.evaluate(_REQUEST_FS_JS))
        except Exception as e:  # noqa: BLE001 - never fail the check on fullscreen
            self._log.debug("fullscreen request errored: %s", e)
            return False

    async def _ensure_fullscreen(
        self, page, video: "VideoHandle", target: TargetConfig, *, had_gesture: bool
    ) -> bool:
        """Programmatically request fullscreen on the configured element.

        Requests fullscreen on ``fullscreen_target`` if set (some players, e.g.
        YouTube, manage fullscreen on a container rather than the <video>), else
        on the video element. The Fullscreen API needs transient user activation:
        we first try using activation from a recent click (e.g. the skip-ad
        click); only if that fails and we have no gesture do we click the video
        (which try_play() will re-start if it pauses).
        """
        from playwright.async_api import Error as PWError
        from playwright.async_api import TimeoutError as PWTimeout

        if target.fullscreen_target:
            fs_locator = page.locator(target.fullscreen_target).first
            what = target.fullscreen_target
        else:
            fs_locator = video.locator
            what = "video element"

        # Attempt 1: use any activation we already have.
        if await self._request_fs(fs_locator):
            self._log.info("entered fullscreen on %s", what)
            return True

        # Attempt 2: manufacture a gesture by clicking the video, then retry.
        if not had_gesture:
            try:
                await video.locator.click(timeout=4000)
            except (PWTimeout, PWError):
                self._log.debug("could not click video for fullscreen gesture")
            if await self._request_fs(fs_locator):
                self._log.info("entered fullscreen on %s", what)
                return True

        self._log.warning(
            "fullscreen NOT achieved on %s (continuing). For a custom player, "
            "pass --fullscreen-id / --fullscreen-target for the right element.", what)
        return False

    async def goto(self, page, url: str) -> None:
        from playwright.async_api import Error as PWError
        from playwright.async_api import TimeoutError as PWTimeout
        try:
            await page.goto(url, timeout=self._cfg.nav_timeout_s * 1000,
                            wait_until="domcontentloaded")
            # Best-effort network settle; do not fail the whole check if the
            # page keeps long-lived connections open.
            try:
                await page.wait_for_load_state("networkidle",
                                               timeout=self._cfg.nav_timeout_s * 1000)
            except PWTimeout:
                self._log.debug("networkidle not reached; continuing")
        except PWTimeout as e:
            raise NavTimeout(f"navigation to {url} timed out") from e
        except PWError as e:
            raise BrowserError(f"navigation error: {e}") from e

    async def locate_video(self, page, target: TargetConfig) -> VideoHandle:
        from playwright.async_api import Error as PWError
        from playwright.async_api import TimeoutError as PWTimeout

        locator = page.locator(target.video_selector).nth(target.video_index)
        try:
            await locator.wait_for(state="attached",
                                   timeout=self._cfg.play_confirm_timeout_s * 1000)
        except PWTimeout as e:
            raise VideoNotFound(
                f"no element for selector {target.video_selector!r} "
                f"index {target.video_index}"
            ) from e

        # Confirm it is actually a media element.
        try:
            tag = await locator.evaluate("el => el.tagName")
        except PWError as e:
            raise BrowserError(f"could not evaluate element: {e}") from e
        if str(tag).upper() != "VIDEO":
            raise NotAMediaElement(
                f"selector {target.video_selector!r} resolved to <{str(tag).lower()}>, "
                f"not <video>"
            )
        return VideoHandle(locator=locator, target=target)

    async def try_play(self, video: VideoHandle) -> None:
        """Trigger playback; classify a rejected play() promise by cause.

        - ``NotAllowedError`` => autoplay policy => AutoplayBlocked.
        - ``NotSupportedError`` / element MediaError set => media/source problem
          => MediaErrorDetected.
        - Anything else => AutoplayBlocked (the conservative, retryable default).
        """
        result = await video.locator.evaluate(_TRY_PLAY_JS)
        if result.get("ok"):
            return
        name = result.get("name") or ""
        error_code = result.get("error_code")
        message = result.get("error") or "play() rejected"
        if name == "NotAllowedError":
            raise AutoplayBlocked(message)
        if name == "NotSupportedError" or error_code is not None:
            raise MediaErrorDetected(error_code if error_code is not None else 4)
        raise AutoplayBlocked(message)

    async def read_state(self, video: VideoHandle) -> MediaSnapshot:
        d = await video.locator.evaluate(_READ_STATE_JS)
        snap = _snapshot_from_dict(d)
        if snap.has_media_keys:
            raise ProtectedContent("DRM / encrypted media keys attached")
        if snap.error_code is not None:
            raise MediaErrorDetected(snap.error_code)
        return snap

    async def read_state_raw(self, video: VideoHandle) -> MediaSnapshot:
        """Like read_state but never raises on error_code (for 'after' reads)."""
        d = await video.locator.evaluate(_READ_STATE_JS)
        return _snapshot_from_dict(d)

    async def wait_for_playback(self, video: VideoHandle) -> bool:
        """Wait until the video has actually started rendering frames.

        Capturing immediately after ``play()`` often yields black frames because
        the media hasn't buffered/painted yet. We poll until the element has a
        current frame available (readyState >= HAVE_CURRENT_DATA), is not paused,
        and ``currentTime`` has advanced past 0 — bounded by the configured
        play-confirm timeout. Returns True if playback started, False on timeout.
        """
        timeout = self._cfg.play_confirm_timeout_s
        deadline = time.monotonic() + timeout
        prev_ct = -1.0
        while time.monotonic() < deadline:
            snap = await self.read_state_raw(video)
            if snap.error_code is not None:
                raise MediaErrorDetected(snap.error_code)
            advancing = snap.current_time > prev_ct
            prev_ct = snap.current_time
            if (snap.ready_state >= 2 and not snap.paused
                    and snap.current_time > 0 and advancing):
                self._log.info("playback started (currentTime=%.2fs, readyState=%d)",
                               snap.current_time, snap.ready_state)
                return True
            await asyncio.sleep(0.25)
        self._log.warning(
            "video did not start rendering within %.1fs (frames may be black); "
            "increase --play-timeout if the source is slow to load.", timeout)
        return False
