"""Typed exceptions mapped to :class:`vpv.models.FailureCode` values.

The Browser Controller raises these so the Orchestrator can translate a
failure into a stable status code without inspecting message strings.
"""

from __future__ import annotations

from .models import FailureCode


class VPVError(Exception):
    """Base for all VPV target-level failures carrying a FailureCode."""

    code: FailureCode = FailureCode.TOOL_ERROR

    def __init__(self, message: str = ""):
        super().__init__(message or self.code.value)
        self.message = message or self.code.value


class NavTimeout(VPVError):
    code = FailureCode.NAV_TIMEOUT


class VideoNotFound(VPVError):
    code = FailureCode.VIDEO_NOT_FOUND


class NotAMediaElement(VPVError):
    code = FailureCode.NOT_A_MEDIA_ELEMENT


class AutoplayBlocked(VPVError):
    code = FailureCode.AUTOPLAY_BLOCKED


class MediaErrorDetected(VPVError):
    code = FailureCode.MEDIA_ERROR

    def __init__(self, error_code: int):
        self.error_code = error_code
        super().__init__(f"media_error_{error_code}")


class ProtectedContent(VPVError):
    code = FailureCode.PROTECTED_CONTENT


class BrowserError(VPVError):
    code = FailureCode.BROWSER_ERROR


class ConfigError(VPVError):
    """Configuration is invalid; surfaces as a tool-level error (exit 2)."""

    code = FailureCode.TOOL_ERROR
