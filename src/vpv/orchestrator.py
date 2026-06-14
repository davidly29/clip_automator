"""Orchestrator (design spec §2.2, §3).

Drives the run lifecycle: navigate -> locate -> play -> capture -> verify ->
persist, per target. Manages bounded concurrency across targets, applies
retries, restarts the browser once on a crash, and aggregates results.
"""

from __future__ import annotations

import asyncio
import random
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from .artifact import ArtifactWriter
from .browser import BrowserController, VideoHandle
from .capture import CaptureEngine
from .config import RunConfig, TargetConfig
from .errors import BrowserError, VPVError
from .logging_setup import get_logger, set_run_id, set_target_id
from .models import FailureCode, RunResult, TargetResult, Verdict
from .verify import evaluate


class Orchestrator:
    def __init__(self, cfg: RunConfig):
        self._cfg = cfg
        self._log = get_logger()
        self._rng = random.Random(cfg.random_seed)

    async def run(self) -> RunResult:
        run_id = uuid.uuid4().hex[:8]
        set_run_id(run_id)
        result = RunResult(run_id=run_id)

        controller = BrowserController(self._cfg)
        try:
            await controller.open()
        except BrowserError as e:
            self._log.error("browser failed to start: %s", e)
            result.tool_error = str(e)
            return result

        try:
            sem = asyncio.Semaphore(self._cfg.concurrency)

            async def guarded(idx: int, target: TargetConfig) -> TargetResult:
                async with sem:
                    return await self._check_with_retries(controller, idx, target)

            tasks = [
                asyncio.create_task(guarded(i, t))
                for i, t in enumerate(self._cfg.targets)
            ]
            result.results = list(await asyncio.gather(*tasks))
        finally:
            await controller.close()

        # A disk-write failure or similar tool-level error on any target
        # escalates the whole run to exit code 2.
        for r in result.results:
            if r.code == FailureCode.TOOL_ERROR:
                result.tool_error = r.error or "tool error"
                break
        return result

    async def _check_with_retries(
        self, controller: BrowserController, idx: int, target: TargetConfig
    ) -> TargetResult:
        set_target_id(f"t{idx}")
        attempts = 0
        last: TargetResult | None = None
        browser_restarted = False

        while attempts <= self._cfg.retries:
            attempts += 1
            try:
                res = await self._check_once(controller, target)
                res.attempts = attempts
                if res.passed or not _is_retryable(res.code):
                    return res
                last = res
                self._log.info("attempt %d failed (%s); retrying", attempts, res.code.value)
            except BrowserError as e:
                self._log.warning("browser error on attempt %d: %s", attempts, e)
                last = TargetResult(
                    url=target.url, passed=False, code=FailureCode.BROWSER_ERROR,
                    reasons=[str(e)], error=str(e), attempts=attempts,
                )
                # Restart the shared browser once — but only when this is the
                # sole target in flight. With concurrency > 1, closing the browser
                # would destroy the contexts of other targets mid-capture, so we
                # just retry with a fresh context instead.
                if not browser_restarted and self._cfg.concurrency == 1:
                    browser_restarted = True
                    try:
                        await controller.close()
                        await controller.open()
                        self._log.info("browser restarted after crash")
                    except BrowserError as e2:
                        last.error = f"browser restart failed: {e2}"
                        return last
        return last  # type: ignore[return-value]

    async def _check_once(
        self, controller: BrowserController, target: TargetConfig
    ) -> TargetResult:
        start = time.monotonic()
        record_clip = self._cfg.capture.mode == "clip"
        rec_dir = tempfile.mkdtemp(prefix="vpv_rec_") if record_clip else None
        context, page = await controller.new_page(record_video_dir=rec_dir)
        video_obj = page.video if record_clip else None
        rec_t0 = time.monotonic()   # ~when Playwright started recording
        selected_video: str | None = None
        fullscreen_ok: bool | None = None
        ad_skipped: bool | None = None
        try:
            await controller.goto(page, target.url)
            # Clear any consent / first-visit modal before doing anything else.
            await controller.dismiss_overlays(page, target)
            # Optional search -> open-result flow that drives how clips are reached.
            await controller.run_interaction(page, target)
            # Optional: randomly pick one of the candidate videos on the homepage.
            selected_video = await controller.pick_random_video(page, target, self._rng)

            video: VideoHandle = await controller.locate_video(page, target)
            # Click play first — on many sites this starts the pre-roll ad — then
            # skip the ad so capture lands on the real content, not the advert.
            await controller.click_play(page, target)
            ad_skipped = await controller.skip_ad(page, target)
            # Now take the content fullscreen and confirm it's actually playing.
            fullscreen_ok = await controller.go_fullscreen(page, video, target)
            await controller.try_play(video)
            # Wait until the video is actually rendering (avoids black frames),
            # then an optional warmup to skip a black/ad intro.
            await controller.wait_for_playback(video)
            if self._cfg.capture.warmup_s > 0:
                await asyncio.sleep(self._cfg.capture.warmup_s)

            before = await controller.read_state(video)
            engine = CaptureEngine(self._cfg.capture)
            # The capture window starts here — after fullscreen has engaged and
            # playback is confirmed — so this offset marks where the clip begins.
            clip_start_offset = time.monotonic() - rec_t0
            capture_t0 = time.monotonic()
            frames = await engine.capture_frames(video)
            clip_duration = time.monotonic() - capture_t0
            after = await controller.read_state_raw(video)

            verdict: Verdict = evaluate(before, after, frames, self._cfg.verification)

            # Flush the recording (if any) by closing the page before reading
            # its path, so the actual video is available to the writer.
            recorded_path = None
            if record_clip and video_obj is not None:
                try:
                    await page.close()
                    recorded_path = Path(await video_obj.path())
                except Exception as e:  # noqa: BLE001 - recording is best-effort
                    self._log.warning("could not finalize recording: %s", e)

            # Persist artifact (proof-of-playback) regardless of pass/fail.
            try:
                writer = ArtifactWriter(self._cfg)
                artifact = writer.write(target, frames, verdict, before, after,
                                        engine, recorded_video=recorded_path,
                                        selected_video=selected_video,
                                        fullscreen=fullscreen_ok,
                                        ad_skipped=ad_skipped,
                                        clip_start_offset=clip_start_offset,
                                        clip_duration=clip_duration)
            except OSError as e:
                self._log.error("disk write failure: %s", e)
                return TargetResult(
                    url=target.url, passed=False, code=FailureCode.TOOL_ERROR,
                    reasons=[f"disk write failure: {e}"], error=str(e),
                    before=before, after=after, selected_video=selected_video,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )

            return TargetResult(
                url=target.url,
                passed=verdict.passed,
                code=verdict.code,
                reasons=verdict.reasons,
                signals=verdict.signals,
                artifact=artifact,
                before=before,
                after=after,
                selected_video=selected_video,
                fullscreen=fullscreen_ok,
                ad_skipped=ad_skipped,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except VPVError as e:
            self._log.info("target failed: %s (%s)", target.url, e.code.value)
            return TargetResult(
                url=target.url, passed=False, code=e.code,
                reasons=[e.message], error=e.message, selected_video=selected_video,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as e:  # noqa: BLE001 - isolate per-target automation errors
            # Unexpected page/automation error (e.g. a navigation destroyed the
            # execution context). Convert to a retryable browser-level failure so
            # a single target can never abort the whole run.
            self._log.warning("unexpected error on %s: %s", target.url, e)
            raise BrowserError(str(e)) from e
        finally:
            try:
                await context.close()
            except Exception:  # noqa: BLE001 - context may already be gone
                pass
            if rec_dir:
                shutil.rmtree(rec_dir, ignore_errors=True)


def _is_retryable(code: FailureCode) -> bool:
    """Only transient/navigation failures are worth retrying."""
    return code in {
        FailureCode.NAV_TIMEOUT,
        FailureCode.BROWSER_ERROR,
        FailureCode.AUTOPLAY_BLOCKED,
    }
