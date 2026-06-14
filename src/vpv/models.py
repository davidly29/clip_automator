"""Shared data structures passed between VPV components.

These are intentionally plain dataclasses with no behaviour so they can be
serialised to JSON (see :mod:`vpv.reporter`) and constructed cheaply in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FailureCode(str, Enum):
    """Stable, machine-readable status codes for a target result.

    The string values appear verbatim in the JSON output and are part of the
    tool's contract with CI pipelines, so do not rename them casually.
    """

    PASS = "pass"
    NAV_TIMEOUT = "nav_timeout"
    VIDEO_NOT_FOUND = "video_not_found"
    NOT_A_MEDIA_ELEMENT = "not_a_media_element"
    AUTOPLAY_BLOCKED = "autoplay_blocked"
    MEDIA_ERROR = "media_error"
    NO_PLAYBACK_PROGRESS = "no_playback_progress"
    FROZEN_FRAMES = "frozen_frames"
    BLANK_OUTPUT = "blank_output"
    PROTECTED_CONTENT = "protected_content"
    BROWSER_ERROR = "browser_error"
    TOOL_ERROR = "tool_error"


@dataclass(frozen=True)
class MediaSnapshot:
    """A point-in-time read of an ``HTMLVideoElement``'s playback state."""

    current_time: float
    duration: float | None
    paused: bool
    ended: bool
    ready_state: int          # HTMLMediaElement.readyState (0-4)
    network_state: int
    error_code: int | None    # MediaError.code if present
    video_width: int
    video_height: int
    has_media_keys: bool = False  # set when EME / DRM is attached

    def to_dict(self) -> dict:
        return {
            "current_time": self.current_time,
            "duration": self.duration,
            "paused": self.paused,
            "ended": self.ended,
            "ready_state": self.ready_state,
            "network_state": self.network_state,
            "error_code": self.error_code,
            "video_width": self.video_width,
            "video_height": self.video_height,
            "has_media_keys": self.has_media_keys,
        }


@dataclass
class Frame:
    """A single captured screenshot of the video element region."""

    index: int
    t_ms: int          # ms after capture started
    png: bytes         # raw PNG bytes


@dataclass
class Verdict:
    """The Verification Engine's decision for one target."""

    passed: bool
    code: FailureCode
    reasons: list[str] = field(default_factory=list)
    signals: dict[str, float | bool | int | None] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "code": self.code.value,
            "reasons": self.reasons,
            "signals": self.signals,
        }


@dataclass
class ArtifactRef:
    """Where an artifact was written on disk."""

    directory: str
    files: list[str] = field(default_factory=list)
    metadata_path: str | None = None

    def to_dict(self) -> dict:
        return {
            "directory": self.directory,
            "files": self.files,
            "metadata_path": self.metadata_path,
        }


@dataclass
class TargetResult:
    """The full outcome of checking one target URL."""

    url: str
    passed: bool
    code: FailureCode
    reasons: list[str] = field(default_factory=list)
    signals: dict = field(default_factory=dict)
    artifact: ArtifactRef | None = None
    before: MediaSnapshot | None = None
    after: MediaSnapshot | None = None
    selected_video: str | None = None  # which candidate was randomly chosen, if any
    fullscreen: bool | None = None     # whether the page entered fullscreen
    ad_skipped: bool | None = None     # whether a pre-roll ad was skipped
    error: str | None = None
    attempts: int = 1
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "passed": self.passed,
            "code": self.code.value,
            "reasons": self.reasons,
            "signals": self.signals,
            "selected_video": self.selected_video,
            "fullscreen": self.fullscreen,
            "ad_skipped": self.ad_skipped,
            "artifact": self.artifact.to_dict() if self.artifact else None,
            "before": self.before.to_dict() if self.before else None,
            "after": self.after.to_dict() if self.after else None,
            "error": self.error,
            "attempts": self.attempts,
            "duration_ms": self.duration_ms,
        }


@dataclass
class RunResult:
    """Aggregate result across all targets in a run."""

    run_id: str
    results: list[TargetResult] = field(default_factory=list)
    tool_error: str | None = None

    @property
    def all_passed(self) -> bool:
        return bool(self.results) and all(r.passed for r in self.results)

    @property
    def exit_code(self) -> int:
        # 2: tool/runtime error; 1: at least one target failed; 0: all passed.
        if self.tool_error is not None:
            return 2
        if not self.results:
            return 2
        return 0 if self.all_passed else 1

    def to_dict(self) -> dict:
        passed = sum(1 for r in self.results if r.passed)
        return {
            "run_id": self.run_id,
            "summary": {
                "total": len(self.results),
                "passed": passed,
                "failed": len(self.results) - passed,
                "exit_code": self.exit_code,
                "tool_error": self.tool_error,
            },
            "targets": [r.to_dict() for r in self.results],
        }
