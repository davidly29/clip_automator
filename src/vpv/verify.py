"""Verification Engine (design spec §4.4).

Decides pass/fail by combining HTMLMediaElement signals with frame-difference
analysis. This module is deliberately pure (no browser, no I/O) so it can be
unit-tested against synthetic frames and crafted MediaSnapshot pairs.

Decision logic (design spec §4.4):
  1. Time advanced: ``after.current_time - before.current_time >= min_time_advance``.
  2. No fatal error: ``error_code`` is None and ``ready_state >= 2``.
  3. Not stuck paused/ended unexpectedly (gates edge cases).
  4. Frame motion: consecutive frames differ above the frozen threshold and are
     not uniformly blank/black.

A pass requires (1) AND (2) AND (4); (3) is recorded and used to refine the
failure code.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from .config import VerificationConfig
from .models import Frame, FailureCode, MediaSnapshot, Verdict

# readyState >= HAVE_CURRENT_DATA means at least the current frame is available.
HAVE_CURRENT_DATA = 2


def _to_gray_array(png: bytes) -> np.ndarray:
    """Decode PNG bytes to a 2-D float32 grayscale array (0-255)."""
    with Image.open(io.BytesIO(png)) as im:
        gray = im.convert("L")
        return np.asarray(gray, dtype=np.float32)


def _frame_metrics(frames: list[Frame]) -> dict:
    """Compute motion and blankness metrics over the captured frames."""
    arrays = [_to_gray_array(f.png) for f in frames]

    # Per-frame standard deviation: a near-zero std means a flat (blank/black
    # or solid white) frame with no rendered content.
    variances = [float(a.std()) for a in arrays]
    max_variance = max(variances) if variances else 0.0

    # Consecutive mean-absolute-difference: the core "is it moving?" signal.
    deltas: list[float] = []
    for a, b in zip(arrays, arrays[1:]):
        if a.shape != b.shape:
            # Resolution changed mid-capture => definitely not frozen.
            deltas.append(float("inf"))
        else:
            deltas.append(float(np.mean(np.abs(a - b))))
    motion_score = max(deltas) if deltas else 0.0
    mean_motion = float(np.mean(deltas)) if deltas else 0.0

    return {
        "motion_score": motion_score,
        "mean_motion": mean_motion,
        "max_variance": max_variance,
        "frame_count": len(frames),
    }


def evaluate(
    before: MediaSnapshot,
    after: MediaSnapshot,
    frames: list[Frame],
    cfg: VerificationConfig,
) -> Verdict:
    reasons: list[str] = []
    signals: dict = {}

    # --- (1) time advanced ---
    time_advance = after.current_time - before.current_time
    time_advanced = time_advance >= cfg.min_time_advance_s
    signals["time_advance_s"] = round(time_advance, 4)
    signals["time_advanced"] = time_advanced
    if time_advanced:
        reasons.append(
            f"currentTime advanced {time_advance:.3f}s "
            f"(>= {cfg.min_time_advance_s}s)"
        )
    else:
        reasons.append(
            f"currentTime advanced only {time_advance:.3f}s "
            f"(< {cfg.min_time_advance_s}s)"
        )

    # --- (2) no fatal error / ready ---
    no_error = after.error_code is None and after.ready_state >= HAVE_CURRENT_DATA
    signals["error_code"] = after.error_code
    signals["ready_state"] = after.ready_state
    signals["no_error"] = no_error
    if after.error_code is not None:
        reasons.append(f"MediaError code {after.error_code} present")
    elif after.ready_state < HAVE_CURRENT_DATA:
        reasons.append(f"readyState {after.ready_state} < {HAVE_CURRENT_DATA} (no data)")

    # --- (3) stuck paused/ended (gate) ---
    stuck_paused = after.paused and not after.ended
    signals["stuck_paused"] = stuck_paused
    signals["ended"] = after.ended
    if stuck_paused:
        reasons.append("element is paused and not ended during capture window")

    # --- (4) frame motion ---
    fm = _frame_metrics(frames)
    signals.update({k: round(v, 4) if isinstance(v, float) else v
                    for k, v in fm.items()})
    blank = fm["max_variance"] <= cfg.blank_frame_max_variance
    has_motion = fm["motion_score"] > cfg.frozen_frame_threshold
    signals["blank"] = blank
    signals["frozen"] = not has_motion
    if blank:
        reasons.append(
            f"all frames blank/black (max variance {fm['max_variance']:.3f} "
            f"<= {cfg.blank_frame_max_variance})"
        )
    elif not has_motion:
        reasons.append(
            f"frames frozen (motion {fm['motion_score']:.3f} "
            f"<= {cfg.frozen_frame_threshold})"
        )
    else:
        reasons.append(f"frames moving (motion {fm['motion_score']:.3f})")

    frame_motion = has_motion and not blank

    passed = time_advanced and no_error and frame_motion
    code = _pick_failure_code(
        passed=passed,
        no_error=no_error,
        after=after,
        time_advanced=time_advanced,
        stuck_paused=stuck_paused,
        blank=blank,
        has_motion=has_motion,
    )
    return Verdict(passed=passed, code=code, reasons=reasons, signals=signals)


def _pick_failure_code(
    *,
    passed: bool,
    no_error: bool,
    after: MediaSnapshot,
    time_advanced: bool,
    stuck_paused: bool,
    blank: bool,
    has_motion: bool,
) -> FailureCode:
    """Choose the most informative failure code (or PASS)."""
    if passed:
        return FailureCode.PASS
    # Hard media errors first.
    if after.error_code is not None or after.ready_state < HAVE_CURRENT_DATA:
        return FailureCode.MEDIA_ERROR
    # Rendering problems.
    if blank:
        return FailureCode.BLANK_OUTPUT
    if not has_motion:
        return FailureCode.FROZEN_FRAMES
    # Playback didn't progress.
    if not time_advanced:
        return FailureCode.NO_PLAYBACK_PROGRESS
    if stuck_paused:
        return FailureCode.NO_PLAYBACK_PROGRESS
    return FailureCode.NO_PLAYBACK_PROGRESS
