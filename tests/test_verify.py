"""Unit tests for the Verification Engine (design spec §8)."""

from __future__ import annotations

from conftest import blank_frames, frozen_frames, moving_frames, snapshot

from vpv.config import VerificationConfig
from vpv.models import FailureCode
from vpv.verify import evaluate

CFG = VerificationConfig()


def test_pass_when_time_advances_and_frames_move():
    v = evaluate(snapshot(0.0), snapshot(3.5), moving_frames(), CFG)
    assert v.passed is True
    assert v.code is FailureCode.PASS
    assert v.signals["time_advanced"] is True
    assert v.signals["frozen"] is False


def test_fail_frozen_frames_even_if_time_advances():
    # currentTime claims progress but the rendered frames never change.
    v = evaluate(snapshot(0.0), snapshot(3.5), frozen_frames(), CFG)
    assert v.passed is False
    assert v.code is FailureCode.FROZEN_FRAMES


def test_fail_blank_output():
    v = evaluate(snapshot(0.0), snapshot(3.5), blank_frames(), CFG)
    assert v.passed is False
    assert v.code is FailureCode.BLANK_OUTPUT


def test_fail_no_playback_progress():
    # Frames move but currentTime never advanced (e.g. looping a buffer w/o play).
    v = evaluate(snapshot(1.0), snapshot(1.0), moving_frames(), CFG)
    assert v.passed is False
    assert v.code is FailureCode.NO_PLAYBACK_PROGRESS


def test_fail_media_error_takes_priority():
    v = evaluate(snapshot(0.0), snapshot(3.5, error_code=4), moving_frames(), CFG)
    assert v.passed is False
    assert v.code is FailureCode.MEDIA_ERROR


def test_fail_low_ready_state():
    v = evaluate(snapshot(0.0), snapshot(3.5, ready_state=1), moving_frames(), CFG)
    assert v.passed is False
    assert v.code is FailureCode.MEDIA_ERROR


def test_paused_and_not_advancing_fails():
    v = evaluate(snapshot(2.0), snapshot(2.0, paused=True), moving_frames(), CFG)
    assert v.passed is False
    assert v.code is FailureCode.NO_PLAYBACK_PROGRESS
    assert v.signals["stuck_paused"] is True


def test_threshold_is_configurable():
    strict = VerificationConfig(min_time_advance_s=5.0)
    v = evaluate(snapshot(0.0), snapshot(3.5), moving_frames(), strict)
    assert v.passed is False
    assert v.code is FailureCode.NO_PLAYBACK_PROGRESS
