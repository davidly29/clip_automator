"""Config Loader (design spec §4.1, §5).

Builds an immutable :class:`RunConfig` from, in increasing precedence:
built-in defaults < config file (JSON/JSONC) < CLI flags / env vars.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from .errors import ConfigError

# --- Known keys, used to reject unknown config-file keys ---------------------
_CAPTURE_KEYS = {"mode", "duration_s", "interval_ms", "frame_count", "width",
                 "height", "warmup_s"}
_VERIFY_KEYS = {"min_time_advance_s", "frozen_frame_threshold", "blank_frame_max_variance"}
_TARGET_KEYS = {
    "url", "video_selector", "video_index",
    "dismiss_selector", "dismiss_id",
    "search_selector", "search_id", "search_query", "search_submit",
    "result_selector", "random_ids", "random_selector", "skip_ad_selector",
    "play_selector", "play_id",
    "fullscreen_selector", "fullscreen_target", "fullscreen_id",
}
_TOP_KEYS = {
    "output_dir", "concurrency", "headless", "nav_timeout_s",
    "play_confirm_timeout_s", "retries", "log_level", "user_agent",
    "browser", "browser_args", "viewport",
    "auto_dismiss_consent", "consent_texts", "fullscreen", "ad_timeout_s",
    "random_seed", "capture", "verification", "targets",
}

DEFAULT_OUTPUT_DIR = Path.home() / "vpv-artifacts"


@dataclass(frozen=True)
class CaptureConfig:
    mode: Literal["frames", "clip"] = "frames"
    duration_s: float = 4.0
    interval_ms: int = 500
    # Default: 2 screenshots (one near the start, one near the end of the
    # window) — enough to detect motion vs frozen, with minimal artifacts. Set
    # to null in a config file to fall back to duration/interval sampling.
    frame_count: int | None = 2
    width: int | None = None
    height: int | None = None
    # Extra wait after playback is confirmed, before capturing — useful to skip a
    # black/ad intro on some players.
    warmup_s: float = 0.0

    def planned_frame_count(self) -> int:
        """How many frames will be sampled given the current settings."""
        if self.frame_count is not None:
            return max(2, self.frame_count)
        n = int((self.duration_s * 1000.0) / self.interval_ms) + 1
        return max(2, n)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "duration_s": self.duration_s,
            "interval_ms": self.interval_ms,
            "frame_count": self.frame_count,
            "width": self.width,
            "height": self.height,
            "warmup_s": self.warmup_s,
        }


@dataclass(frozen=True)
class VerificationConfig:
    min_time_advance_s: float = 0.25
    frozen_frame_threshold: float = 2.0      # mean abs pixel delta
    blank_frame_max_variance: float = 1.0

    def to_dict(self) -> dict:
        return {
            "min_time_advance_s": self.min_time_advance_s,
            "frozen_frame_threshold": self.frozen_frame_threshold,
            "blank_frame_max_variance": self.blank_frame_max_variance,
        }


@dataclass(frozen=True)
class TargetConfig:
    """One target site plus the interaction flow used to reach a playing video.

    All interaction fields are optional: with none of them set, VPV behaves as
    the simple "page already has a <video>" verifier. With them set, VPV drives
    a search -> open-result -> play -> fullscreen flow before capturing.
    """

    url: str
    video_selector: str = "video"
    video_index: int = 0
    # --- entry overlays (consent / age gate / cookie modal) ---
    dismiss_selectors: tuple[str, ...] = ()  # one or more OK/Accept elements to click
    # --- interaction flow (all optional) ---
    search_selector: str | None = None      # search bar, e.g. "#search" or "input[name=q]"
    search_query: str | None = None         # text typed into the search bar
    search_submit: str | None = None        # submit button; None => press Enter
    result_selector: str | None = None       # search result to click to open the video
    # --- random pick from the homepage ---
    random_ids: tuple[str, ...] = ()        # candidate video element ids
    random_selector: str | None = None       # OR a CSS selector; one match picked at random
    # --- pre-roll ad ---
    skip_ad_selector: str | None = None      # element to click to skip a pre-roll ad
    # --- player controls ---
    play_selector: str | None = None        # play button to click
    fullscreen_selector: str | None = None  # fullscreen button to click
    fullscreen_target: str | None = None     # element to call requestFullscreen() on
                                            # (defaults to the video element)

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "video_selector": self.video_selector,
            "video_index": self.video_index,
            "dismiss_selectors": list(self.dismiss_selectors),
            "search_selector": self.search_selector,
            "search_query": self.search_query,
            "search_submit": self.search_submit,
            "result_selector": self.result_selector,
            "random_ids": list(self.random_ids),
            "random_selector": self.random_selector,
            "skip_ad_selector": self.skip_ad_selector,
            "play_selector": self.play_selector,
            "fullscreen_selector": self.fullscreen_selector,
            "fullscreen_target": self.fullscreen_target,
        }


@dataclass(frozen=True)
class RunConfig:
    targets: tuple[TargetConfig, ...]
    output_dir: Path
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    verification: VerificationConfig = field(default_factory=VerificationConfig)
    nav_timeout_s: float = 30.0
    play_confirm_timeout_s: float = 10.0
    retries: int = 1
    concurrency: int = 2
    headless: bool = True
    browser: Literal["chromium", "firefox", "webkit"] = "chromium"
    browser_args: tuple[str, ...] = ()      # extra flags passed to the browser launch
    viewport_width: int = 1920              # browser viewport + clip recording size
    viewport_height: int = 1080
    user_agent: str | None = None
    log_level: str = "INFO"
    auto_dismiss_consent: bool = True       # auto-click common consent/OK buttons
    consent_texts: tuple[str, ...] = ()     # override accept-button phrases (() = built-in)
    fullscreen: bool = True                 # put the video fullscreen before capture
    ad_timeout_s: float = 15.0              # how long to wait for a skip-ad control
    random_seed: int | None = None          # seed for random video selection

    def to_dict(self) -> dict:
        return {
            "output_dir": str(self.output_dir),
            "concurrency": self.concurrency,
            "headless": self.headless,
            "browser": self.browser,
            "browser_args": list(self.browser_args),
            "viewport": f"{self.viewport_width}x{self.viewport_height}",
            "nav_timeout_s": self.nav_timeout_s,
            "play_confirm_timeout_s": self.play_confirm_timeout_s,
            "retries": self.retries,
            "log_level": self.log_level,
            "user_agent": self.user_agent,
            "auto_dismiss_consent": self.auto_dismiss_consent,
            "consent_texts": list(self.consent_texts),
            "fullscreen": self.fullscreen,
            "ad_timeout_s": self.ad_timeout_s,
            "random_seed": self.random_seed,
            "capture": self.capture.to_dict(),
            "verification": self.verification.to_dict(),
            "targets": [t.to_dict() for t in self.targets],
        }


# --- JSONC support: strip // and /* */ comments before json.loads ------------
_LINE_COMMENT = re.compile(r"(^|[^:])//[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_jsonc(text: str) -> str:
    text = _BLOCK_COMMENT.sub("", text)
    # Remove // line comments while leaving "http://" inside strings mostly
    # intact. The negative lookbehind on ':' is a pragmatic guard.
    return _LINE_COMMENT.sub(lambda m: m.group(1), text)


def _resolve_selector(selector: str | None, an_id: str | None) -> str | None:
    """Accept either a full CSS selector or a bare HTML id (turned into #id)."""
    if selector:
        return selector
    if an_id:
        sid = an_id.strip().lstrip("#")
        return f"#{sid}" if sid else None
    return None


def _coerce_str_list(raw, where: str, key: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise ConfigError(f"{where}{key} must be a list of strings")
    return tuple(x.strip() for x in raw if x.strip())


def _as_list(v) -> list[str]:
    """Accept None, a single string, or a list of strings -> list of strings."""
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return list(v)
    raise ConfigError("expected a string or list of strings")


def _resolve_dismiss(selectors, ids) -> tuple[str, ...]:
    """Build the ordered list of overlay-dismiss selectors from CSS selectors
    and/or bare HTML ids (each turned into #id)."""
    out: list[str] = []
    for s in _as_list(selectors):
        s = s.strip()
        if s:
            out.append(s)
    for i in _as_list(ids):
        i = i.strip().lstrip("#")
        if i:
            out.append(f"#{i}")
    return tuple(out)


def _parse_viewport(value: str) -> tuple[int, int]:
    m = re.match(r"^\s*(\d+)\s*[xX]\s*(\d+)\s*$", value)
    if not m:
        raise ConfigError(f"viewport must be WIDTHxHEIGHT (e.g. 1920x1080), got {value!r}")
    w, h = int(m.group(1)), int(m.group(2))
    if w <= 0 or h <= 0:
        raise ConfigError("viewport width and height must be > 0")
    return w, h


def _reject_unknown(d: dict, allowed: set[str], where: str) -> None:
    extra = set(d) - allowed
    if extra:
        raise ConfigError(f"Unknown {where} key(s): {', '.join(sorted(extra))}")


# --- .properties profile support -------------------------------------------
# Flat key=value files (one per website) that map onto the config schema.
_PROP_BOOL = {"headless", "auto_dismiss_consent", "fullscreen"}
_PROP_INT = {"concurrency", "retries", "video_index", "frame_count", "width",
             "height", "interval_ms", "random_seed"}
_PROP_FLOAT = {"nav_timeout_s", "play_confirm_timeout_s", "duration_s", "warmup_s",
               "ad_timeout_s", "min_time_advance_s", "frozen_frame_threshold",
               "blank_frame_max_variance"}
_PROP_LIST = {"consent_texts", "browser_args", "random_ids",
              "dismiss_selector", "dismiss_id"}
_PROP_CAPTURE = {"mode", "duration_s", "interval_ms", "frame_count", "width",
                 "height", "warmup_s"}
_PROP_VERIFY = {"min_time_advance_s", "frozen_frame_threshold", "blank_frame_max_variance"}
_PROP_TARGET = {
    "url", "video_selector", "video_index", "dismiss_selector", "dismiss_id",
    "search_selector", "search_id", "search_query", "search_submit",
    "result_selector", "random_ids", "random_selector", "skip_ad_selector",
    "play_selector", "play_id", "fullscreen_selector", "fullscreen_target",
    "fullscreen_id",
}


def _coerce_prop(key: str, value: str):
    """Coerce a raw string properties value to the type the schema expects."""
    v = value.strip()
    if key in _PROP_BOOL:
        low = v.lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
        raise ConfigError(f"property {key!r} must be true/false, got {value!r}")
    if key in _PROP_INT:
        try:
            return int(v)
        except ValueError as e:
            raise ConfigError(f"property {key!r} must be an integer, got {value!r}") from e
    if key in _PROP_FLOAT:
        try:
            return float(v)
        except ValueError as e:
            raise ConfigError(f"property {key!r} must be a number, got {value!r}") from e
    if key in _PROP_LIST:
        return [x.strip() for x in v.split(",") if x.strip()]
    return v


def _parse_properties(text: str) -> dict:
    """Parse a flat key=value (or key:value) properties file into a config dict.

    Lines starting with # or ! are comments. Keys mirror the CLI/config names;
    capture/verification/target keys are routed into their sub-objects so the
    rest of the loader treats a profile exactly like a JSON config.
    """
    flat: dict = {}
    for n, line in enumerate(text.splitlines(), start=1):
        s = line.strip()
        if not s or s[0] in "#!":
            continue
        # Split on the FIRST '=' or ':' only, so values may contain either
        # (e.g. "skip_ad_selector = text=Skip ad" or a URL with "://").
        positions = [i for i in (s.find("="), s.find(":")) if i != -1]
        if not positions:
            raise ConfigError(f"profile line {n}: expected 'key = value', got {line!r}")
        idx = min(positions)
        k, val = s[:idx], s[idx + 1:]
        if not k.strip():
            raise ConfigError(f"profile line {n}: missing key, got {line!r}")
        flat[k.strip()] = val

    cfg: dict = {}
    capture: dict = {}
    verification: dict = {}
    target: dict = {}
    for k, raw in flat.items():
        coerced = _coerce_prop(k, raw)
        if k in _PROP_CAPTURE:
            capture[k] = coerced
        elif k in _PROP_VERIFY:
            verification[k] = coerced
        elif k in _PROP_TARGET:
            target[k] = coerced
        else:
            cfg[k] = coerced   # top-level run key (validated later)
    if capture:
        cfg["capture"] = capture
    if verification:
        cfg["verification"] = verification
    if target:
        cfg["targets"] = [target]
    return cfg


def _profile_search_dirs(profiles_dir: str | None) -> list[Path]:
    dirs: list[Path] = []
    if profiles_dir:
        dirs.append(Path(profiles_dir))
    env = os.environ.get("VPV_PROFILES_DIR")
    if env:
        dirs.append(Path(env))
    dirs.append(Path.cwd() / "profiles")
    dirs.append(Path.home() / ".vpv" / "profiles")
    return dirs


def _resolve_profile(name: str, profiles_dir: str | None) -> Path:
    """Find a profile file by name across the profile search directories."""
    direct = Path(name)
    if direct.is_file():
        return direct
    exts = ["", ".properties", ".jsonc", ".json"]
    searched: list[str] = []
    for d in _profile_search_dirs(profiles_dir):
        for ext in exts:
            cand = d / f"{name}{ext}"
            searched.append(str(cand))
            if cand.is_file():
                return cand
    raise ConfigError(
        f"profile {name!r} not found. Looked in:\n  " + "\n  ".join(searched))


def _load_file(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"Cannot read config/profile file {path}: {e}") from e
    if path.suffix.lower() == ".properties":
        data = _parse_properties(raw)
    else:
        try:
            data = json.loads(_strip_jsonc(raw))
        except json.JSONDecodeError as e:
            raise ConfigError(f"Invalid JSON in {path}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError("Config file must contain an object at the top level")
    _reject_unknown(data, _TOP_KEYS, "config")
    return data


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vpv",
        description="Verify that videos on a web page actually play and capture "
                    "a proof-of-playback snippet.",
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument("--config", type=Path, help="Path to a JSON/JSONC/.properties config file.")
    src.add_argument("--profile", help="Named profile to load (e.g. 'mysite'); resolved from "
                                       "./profiles, $VPV_PROFILES_DIR, or ~/.vpv/profiles.")
    p.add_argument("--profiles-dir", help="Extra directory to search for --profile files.")
    p.add_argument("--url", help="Target page URL (single-target shorthand).")
    p.add_argument("--video-selector", help="CSS selector for the video element.")
    p.add_argument("--video-index", type=int, help="Which match if selector returns many.")
    # --- entry overlays (consent / age gate / cookie modal) ---
    p.add_argument("--dismiss-selector", dest="dismiss_selectors", action="append",
                   help="CSS selector of an overlay element to click (repeatable).")
    p.add_argument("--dismiss-id", dest="dismiss_ids", action="append",
                   help="HTML id of an overlay element to click (repeatable; => #id).")
    auto = p.add_mutually_exclusive_group()
    auto.add_argument("--auto-dismiss", dest="auto_dismiss_consent",
                      action="store_true", default=None,
                      help="Auto-click common consent/OK buttons (default: on).")
    auto.add_argument("--no-auto-dismiss", dest="auto_dismiss_consent", action="store_false")
    p.add_argument("--consent-text", dest="consent_texts", action="append",
                   help="Accept-button phrase to match for auto-dismiss (repeatable; "
                        "replaces the built-in phrase list).")
    fs = p.add_mutually_exclusive_group()
    fs.add_argument("--fullscreen", dest="fullscreen", action="store_true", default=None,
                    help="Put the video fullscreen before capture (default: on).")
    fs.add_argument("--no-fullscreen", dest="fullscreen", action="store_false")
    # --- random pick from the homepage ---
    p.add_argument("--random-id", dest="random_ids", action="append",
                   help="Candidate video id to pick from at random (repeatable).")
    p.add_argument("--random-selector",
                   help="CSS selector; one matching element is picked at random and clicked "
                        "(e.g. 'div[id^=video_]').")
    p.add_argument("--random-seed", type=int, help="Seed for reproducible random selection.")
    # --- pre-roll ad ---
    p.add_argument("--skip-ad-selector",
                   help="Element to click to skip a pre-roll ad (e.g. 'text=Skip ad'); "
                        "VPV waits for it to become clickable.")
    p.add_argument("--ad-timeout", dest="ad_timeout_s", type=float,
                   help="Max seconds to wait for the skip-ad control to appear (default 15).")
    # --- interaction flow (single-target shorthand) ---
    p.add_argument("--search-selector", help="CSS selector for the search bar.")
    p.add_argument("--search-id", help="HTML id of the search bar (shorthand for #id).")
    p.add_argument("--search-query", help="Text to type into the search bar.")
    p.add_argument("--search-submit", help="CSS selector for the search submit button "
                                           "(default: press Enter).")
    p.add_argument("--result-selector", help="CSS selector for the search result to click "
                                             "to open the video.")
    p.add_argument("--play-selector", help="CSS selector for the play button.")
    p.add_argument("--play-id", help="HTML id of the play button (shorthand for #id).")
    p.add_argument("--fullscreen-selector", help="CSS selector for a fullscreen button to click.")
    p.add_argument("--fullscreen-target",
                   help="CSS selector of the element to make fullscreen "
                        "(default: the video element).")
    p.add_argument("--fullscreen-id",
                   help="HTML id of the element to make fullscreen (shorthand for #id).")
    p.add_argument("--output-dir", type=Path, help="Directory for artifacts.")
    p.add_argument("--mode", choices=["frames", "clip"], help="Capture mode.")
    p.add_argument("--duration", dest="duration_s", type=float, help="Capture window seconds.")
    p.add_argument("--interval-ms", type=int, help="Frame sampling interval (frames mode).")
    p.add_argument("--frame-count", type=int, help="Fixed frame count (overrides duration/interval).")
    p.add_argument("--width", type=int, help="Downscale width for captured frames.")
    p.add_argument("--height", type=int, help="Downscale height for captured frames.")
    p.add_argument("--warmup", dest="warmup_s", type=float,
                   help="Seconds to wait after playback starts before capturing "
                        "(skip a black/ad intro).")
    p.add_argument("--nav-timeout", dest="nav_timeout_s", type=float, help="Navigation timeout seconds.")
    p.add_argument("--play-timeout", dest="play_confirm_timeout_s", type=float,
                   help="Playback-confirm timeout seconds.")
    p.add_argument("--retries", type=int, help="Retries per target on transient failure.")
    p.add_argument("--concurrency", type=int, help="Max targets checked in parallel.")
    p.add_argument("--browser", choices=["chromium", "firefox", "webkit"], help="Browser engine.")
    p.add_argument("--browser-arg", dest="browser_args", action="append",
                   help="Extra flag passed to the browser launch, e.g. "
                        "--browser-arg=--disable-gpu (repeatable; use the = form).")
    p.add_argument("--viewport", help="Viewport + clip recording size, e.g. 1920x1080 "
                                      "(default). The clip is recorded at this size.")
    p.add_argument("--user-agent", help="Custom, identifiable User-Agent string.")
    p.add_argument("--log-level", help="DEBUG/INFO/WARNING/ERROR.")
    p.add_argument("--result-file", type=Path, help="Also write the JSON result to this path.")
    headless = p.add_mutually_exclusive_group()
    headless.add_argument("--headless", dest="headless", action="store_true", default=None)
    headless.add_argument("--headed", dest="headless", action="store_false")
    return p


def _coerce_targets(raw: Any) -> list[TargetConfig]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError("'targets' must be a non-empty list")
    out: list[TargetConfig] = []
    for i, t in enumerate(raw):
        if not isinstance(t, dict):
            raise ConfigError(f"targets[{i}] must be an object")
        _reject_unknown(t, _TARGET_KEYS, f"targets[{i}]")
        if "url" not in t or not isinstance(t["url"], str) or not t["url"].strip():
            raise ConfigError(f"targets[{i}] requires a non-empty 'url'")
        out.append(TargetConfig(
            url=t["url"].strip(),
            video_selector=t.get("video_selector", "video"),
            video_index=int(t.get("video_index", 0)),
            dismiss_selectors=_resolve_dismiss(
                t.get("dismiss_selector"), t.get("dismiss_id")),
            search_selector=_resolve_selector(
                t.get("search_selector"), t.get("search_id")),
            search_query=t.get("search_query"),
            search_submit=t.get("search_submit"),
            result_selector=t.get("result_selector"),
            random_ids=_coerce_str_list(t.get("random_ids"), f"targets[{i}]", ".random_ids"),
            random_selector=t.get("random_selector"),
            skip_ad_selector=t.get("skip_ad_selector"),
            play_selector=_resolve_selector(t.get("play_selector"), t.get("play_id")),
            fullscreen_selector=t.get("fullscreen_selector"),
            fullscreen_target=_resolve_selector(
                t.get("fullscreen_target"), t.get("fullscreen_id")),
        ))
    return out


def load_config(argv: list[str]) -> tuple[RunConfig, Path | None]:
    """Parse argv into a validated, immutable RunConfig.

    Returns the config plus an optional result-file path requested on the CLI.
    Raises :class:`ConfigError` on any validation problem.
    """
    args = _build_parser().parse_args(argv)

    file_data: dict = {}
    if args.profile:
        file_data = _load_file(_resolve_profile(args.profile, args.profiles_dir))
    elif args.config:
        file_data = _load_file(args.config)

    # --- capture: file then CLI overrides ---
    cap_raw = file_data.get("capture", {}) or {}
    if not isinstance(cap_raw, dict):
        raise ConfigError("'capture' must be an object")
    _reject_unknown(cap_raw, _CAPTURE_KEYS, "capture")
    # frame_count defaults to 2; a CLI value wins, then an explicit config value
    # (including null => duration/interval sampling), else the default of 2.
    if args.frame_count is not None:
        frame_count = args.frame_count
    elif "frame_count" in cap_raw:
        frame_count = cap_raw["frame_count"]   # may be None => interval sampling
    else:
        frame_count = 2
    capture = CaptureConfig(
        mode=args.mode or cap_raw.get("mode", "frames"),
        duration_s=_pick(args.duration_s, cap_raw.get("duration_s"), 4.0),
        interval_ms=_pick(args.interval_ms, cap_raw.get("interval_ms"), 500),
        frame_count=frame_count,
        width=_pick(args.width, cap_raw.get("width"), None),
        height=_pick(args.height, cap_raw.get("height"), None),
        warmup_s=_pick(args.warmup_s, cap_raw.get("warmup_s"), 0.0),
    )

    # --- verification: file only (plus defaults) ---
    ver_raw = file_data.get("verification", {}) or {}
    if not isinstance(ver_raw, dict):
        raise ConfigError("'verification' must be an object")
    _reject_unknown(ver_raw, _VERIFY_KEYS, "verification")
    verification = VerificationConfig(
        min_time_advance_s=ver_raw.get("min_time_advance_s", 0.25),
        frozen_frame_threshold=ver_raw.get("frozen_frame_threshold", 2.0),
        blank_frame_max_variance=ver_raw.get("blank_frame_max_variance", 1.0),
    )

    # --- targets: CLI --url shorthand wins over file 'targets' ---
    if args.url:
        targets = [TargetConfig(
            url=args.url.strip(),
            video_selector=args.video_selector or "video",
            video_index=args.video_index or 0,
            dismiss_selectors=_resolve_dismiss(args.dismiss_selectors, args.dismiss_ids),
            search_selector=_resolve_selector(args.search_selector, args.search_id),
            search_query=args.search_query,
            search_submit=args.search_submit,
            result_selector=args.result_selector,
            random_ids=tuple(args.random_ids or ()),
            random_selector=args.random_selector,
            skip_ad_selector=args.skip_ad_selector,
            play_selector=_resolve_selector(args.play_selector, args.play_id),
            fullscreen_selector=args.fullscreen_selector,
            fullscreen_target=_resolve_selector(args.fullscreen_target, args.fullscreen_id),
        )]
    elif "targets" in file_data:
        targets = _coerce_targets(file_data["targets"])
    else:
        raise ConfigError("No targets: pass --url or a config file with 'targets'.")

    output_dir = (
        args.output_dir
        or (Path(file_data["output_dir"]) if "output_dir" in file_data else None)
        or DEFAULT_OUTPUT_DIR
    ).expanduser()

    user_agent = args.user_agent or file_data.get("user_agent") or os.environ.get("VPV_USER_AGENT")

    viewport_raw = args.viewport or file_data.get("viewport")
    viewport_w, viewport_h = _parse_viewport(viewport_raw) if viewport_raw else (1920, 1080)

    cfg = RunConfig(
        targets=tuple(targets),
        output_dir=output_dir,
        capture=capture,
        verification=verification,
        nav_timeout_s=_pick(args.nav_timeout_s, file_data.get("nav_timeout_s"), 30.0),
        play_confirm_timeout_s=_pick(args.play_confirm_timeout_s,
                                     file_data.get("play_confirm_timeout_s"), 10.0),
        retries=_pick(args.retries, file_data.get("retries"), 1),
        concurrency=_pick(args.concurrency, file_data.get("concurrency"), 2),
        headless=_pick(args.headless, file_data.get("headless"), True),
        browser=args.browser or file_data.get("browser", "chromium"),
        browser_args=(tuple(args.browser_args) if args.browser_args
                      else _coerce_str_list(file_data.get("browser_args"),
                                            "", "browser_args")),
        viewport_width=viewport_w,
        viewport_height=viewport_h,
        user_agent=user_agent,
        log_level=(args.log_level or file_data.get("log_level", "INFO")).upper(),
        auto_dismiss_consent=_pick(args.auto_dismiss_consent,
                                   file_data.get("auto_dismiss_consent"), True),
        consent_texts=(tuple(args.consent_texts) if args.consent_texts
                       else _coerce_str_list(file_data.get("consent_texts"),
                                             "", "consent_texts")),
        fullscreen=_pick(args.fullscreen, file_data.get("fullscreen"), True),
        ad_timeout_s=_pick(args.ad_timeout_s, file_data.get("ad_timeout_s"), 15.0),
        random_seed=_pick(args.random_seed, file_data.get("random_seed"), None),
    )
    _validate(cfg)
    return cfg, args.result_file


def _pick(cli: Any, file_val: Any, default: Any) -> Any:
    """First non-None of CLI override, file value, built-in default."""
    if cli is not None:
        return cli
    if file_val is not None:
        return file_val
    return default


def _validate(cfg: RunConfig) -> None:
    if cfg.capture.duration_s <= 0:
        raise ConfigError("capture.duration_s must be > 0")
    if cfg.capture.interval_ms <= 0:
        raise ConfigError("capture.interval_ms must be > 0")
    if cfg.capture.frame_count is not None and cfg.capture.frame_count < 2:
        raise ConfigError("capture.frame_count must be >= 2")
    if cfg.concurrency < 1:
        raise ConfigError("concurrency must be >= 1")
    if cfg.retries < 0:
        raise ConfigError("retries must be >= 0")
    if cfg.nav_timeout_s <= 0 or cfg.play_confirm_timeout_s <= 0:
        raise ConfigError("timeouts must be > 0")
    for t in cfg.targets:
        if t.video_index < 0:
            raise ConfigError("video_index must be >= 0")
        if not re.match(r"^https?://", t.url, re.IGNORECASE):
            raise ConfigError(f"target url must start with http:// or https://: {t.url}")
        if t.search_query is not None and not t.search_selector:
            raise ConfigError(
                "search_query requires a search bar (set search_selector or search_id)")
        if t.search_submit and not t.search_selector:
            raise ConfigError("search_submit requires search_selector/search_id")
    if cfg.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigError(f"invalid log_level: {cfg.log_level}")
    _check_writable(cfg.output_dir)


def _check_writable(output_dir: Path) -> None:
    """Ensure the output directory exists (creating it) and is writable."""
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        probe = output_dir / ".vpv_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        raise ConfigError(f"output_dir is not writable ({output_dir}): {e}") from e


def with_overrides(cfg: RunConfig, **kw) -> RunConfig:
    """Test helper: produce a copy of a RunConfig with fields replaced."""
    return replace(cfg, **kw)
