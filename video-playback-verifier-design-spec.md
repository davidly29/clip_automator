# Design Specification — Video Playback Verifier (VPV)

**Document type:** Software design specification
**Status:** Draft v1.0
**Component class:** QA / synthetic-monitoring CLI tool

---

## 1. Overview & Goals

### 1.1 Summary
The Video Playback Verifier (VPV) is a command-line tool used in QA and synthetic-monitoring pipelines to confirm that videos embedded on a target web page **load and actually play**. As evidence of each check, VPV captures a short snippet preview (a small sequence of frames or a brief clip) of the target video and writes it to a configurable local directory. It then emits a machine-readable pass/fail result.

### 1.2 Goals
- Determine, with reasonable confidence, whether a specified video on a page is *playing* — not merely present in the DOM.
- Produce a small, time-bounded artifact (the "snippet") as proof of playback for audit and debugging.
- Integrate cleanly into CI/CD and scheduled monitoring (exit codes + JSON output).
- Be configurable, observable, and robust against the common failure modes of web video.

### 1.3 Explicit non-goals
- VPV is **not** a media downloader, archiver, or redistribution tool. Captured snippets are minimal verification artifacts, not a content library.
- VPV does not transcode, re-host, or publish captured media.
- VPV does not attempt to bypass paywalls, DRM, authentication, or access controls.

### 1.4 Primary users
QA engineers and SREs running automated checks against websites they own or are authorized to test.

---

## 2. Architecture

### 2.1 High-level component diagram (textual)

```
            ┌──────────────────────────────────────────────────────┐
   CLI ───▶ │  Config Loader  ──▶  Orchestrator                     │
            │                          │                            │
            │        ┌─────────────────┼──────────────────┐         │
            │        ▼                 ▼                  ▼          │
            │  Browser Controller  Capture Engine   Verification     │
            │  (Playwright)        (frames/clip)     Engine          │
            │        │                 │                  │          │
            │        └────────┬────────┴────────┬─────────┘          │
            │                 ▼                  ▼                    │
            │          Artifact Writer     Result Reporter           │
            │          (local disk)        (JSON + exit code)        │
            └──────────────────────────────────────────────────────┘
                                 │
                         Structured Logger (cross-cutting)
```

### 2.2 Component responsibilities

| Component | Responsibility |
|---|---|
| **Config Loader** | Parse CLI flags, env vars, and optional config file; validate against schema; produce an immutable `RunConfig`. |
| **Orchestrator** | Drive the run lifecycle, manage concurrency across targets, aggregate results. |
| **Browser Controller** | Launch a headless browser, navigate to the page, locate the video element, trigger/confirm playback, and read media-element state. |
| **Capture Engine** | Sample frames or record a short clip during playback. |
| **Verification Engine** | Decide pass/fail by combining media-element signals and frame-difference analysis. |
| **Artifact Writer** | Persist the snippet and a metadata sidecar to the configured directory with a deterministic naming scheme. |
| **Result Reporter** | Emit a JSON result object and set the process exit code. |
| **Structured Logger** | Cross-cutting, level-based logging with run/target correlation IDs. |

---

## 3. Data Flow

```
input (domain/url + video selector + config)
        │
        ▼
[navigate] Browser Controller loads page, waits for network/idle
        │
        ▼
[locate]   Resolve video element via selector; confirm it exists & is a media element
        │
        ▼
[play]     Attempt playback (respect autoplay rules; unmute/mute as needed)
        │
        ▼
[capture]  Capture Engine samples N frames over T seconds (or records clip)
        │
        ▼
[verify]   Verification Engine evaluates:
             • media-element signals (currentTime advancing, no error, readyState)
             • frame deltas (frames change over time, not black/frozen)
        │
        ├──▶ [persist] Artifact Writer saves snippet + metadata.json
        │
        ▼
[report]   Result Reporter writes JSON result, sets exit code (0 pass / non-zero fail)
```

---

## 4. Module Specifications

Interfaces below are shown in Python type-hint style for clarity; the same contracts apply if a Node/TypeScript stack is chosen (see §7).

### 4.1 Config Loader

```python
@dataclass(frozen=True)
class CaptureConfig:
    mode: Literal["frames", "clip"] = "frames"
    duration_s: float = 4.0           # capture window length
    interval_ms: int = 500            # sampling interval for frame mode
    frame_count: int | None = None    # overrides duration/interval if set
    width: int | None = None          # optional downscale; None = native
    height: int | None = None

@dataclass(frozen=True)
class TargetConfig:
    url: str
    video_selector: str = "video"     # CSS selector; default first <video>
    video_index: int = 0              # which match if selector returns many

@dataclass(frozen=True)
class RunConfig:
    targets: list[TargetConfig]
    output_dir: Path
    capture: CaptureConfig
    nav_timeout_s: float = 30.0
    play_confirm_timeout_s: float = 10.0
    retries: int = 1
    concurrency: int = 2
    headless: bool = True
    user_agent: str | None = None
    log_level: str = "INFO"

def load_config(argv: list[str]) -> RunConfig: ...
```

Validation rules: `output_dir` must be writable; `duration_s > 0`; `concurrency >= 1`; `video_index >= 0`; unknown keys rejected.

### 4.2 Browser Controller

```python
class BrowserController:
    async def open(self, cfg: RunConfig) -> None: ...
    async def goto(self, url: str) -> None: ...
    async def locate_video(self, selector: str, index: int) -> VideoHandle: ...
    async def try_play(self, video: VideoHandle) -> PlaybackState: ...
    async def read_state(self, video: VideoHandle) -> MediaSnapshot: ...
    async def close(self) -> None: ...

@dataclass
class MediaSnapshot:
    current_time: float
    duration: float | None
    paused: bool
    ended: bool
    ready_state: int          # HTMLMediaElement.readyState (0–4)
    network_state: int
    error_code: int | None    # MediaError.code if present
    video_width: int
    video_height: int
```

`read_state` is implemented by evaluating JS in page context against the resolved `HTMLVideoElement`. `try_play` handles autoplay restrictions by muting before calling `.play()` and surfacing rejected play promises.

### 4.3 Capture Engine

```python
class CaptureEngine:
    async def capture_frames(
        self, video: VideoHandle, cfg: CaptureConfig
    ) -> list[Frame]: ...

    async def capture_clip(
        self, video: VideoHandle, cfg: CaptureConfig
    ) -> ClipArtifact: ...
```

- **frames mode (default):** take screenshots of the video element bounding box at each interval. Lightweight, no extra binaries, sufficient for verification.
- **clip mode:** record a short video of the element region (browser video recording, or assemble sampled frames into an mp4 via ffmpeg). Used when a moving artifact is preferred for human review.

### 4.4 Verification Engine

```python
@dataclass
class Verdict:
    passed: bool
    reasons: list[str]            # human-readable signal explanations
    signals: dict[str, float|bool|int]

class VerificationEngine:
    def evaluate(
        self,
        before: MediaSnapshot,
        after: MediaSnapshot,
        frames: list[Frame],
    ) -> Verdict: ...
```

Decision logic (all weighted, configurable thresholds):
1. **Time advanced:** `after.current_time > before.current_time` by a meaningful delta. Strong positive signal.
2. **No fatal error:** `error_code` is null and `ready_state >= 2`.
3. **Not stuck paused/ended unexpectedly** during the capture window.
4. **Frame motion:** mean pixel difference between consecutive frames exceeds a "frozen frame" threshold, and frames are not uniformly black/blank. Guards against a video element that reports playing but renders nothing.

A pass requires (1) **and** (2) **and** (4); (3) gates edge cases. Reasons array records which signals fired.

### 4.5 Artifact Writer

```python
class ArtifactWriter:
    def write(self, target: TargetConfig, frames_or_clip, verdict: Verdict) -> ArtifactRef: ...
```

Naming scheme:
```
{output_dir}/{YYYYMMDD-HHMMSS}_{domain}_{video-id-or-index}/
    frame_000.png ... frame_00N.png   (frames mode)
    snippet.mp4                        (clip mode)
    metadata.json                      (verdict + MediaSnapshots + config used)
```

### 4.6 Result Reporter

Emits one JSON object per target plus an aggregate, to stdout and optionally a file. Process exit code: `0` if all targets passed, `1` if any failed, `2` on tool/runtime error (config invalid, browser crash).

---

## 5. Configuration Schema

```jsonc
{
  "output_dir": "/var/qa/vpv-artifacts",     // required, writable
  "concurrency": 2,
  "headless": true,
  "nav_timeout_s": 30,
  "play_confirm_timeout_s": 10,
  "retries": 1,
  "log_level": "INFO",
  "capture": {
    "mode": "frames",                          // "frames" | "clip"
    "duration_s": 4.0,
    "interval_ms": 500,
    "frame_count": null,
    "width": null,
    "height": null
  },
  "verification": {
    "min_time_advance_s": 0.25,
    "frozen_frame_threshold": 2.0,             // mean abs pixel delta
    "blank_frame_max_variance": 1.0
  },
  "targets": [
    { "url": "https://example.com/watch/123", "video_selector": "video", "video_index": 0 }
  ]
}
```

CLI flags mirror top-level keys (e.g. `--output-dir`, `--concurrency`, `--mode`); flags override file values, which override built-in defaults.

---

## 6. Error Handling & Edge Cases

| Condition | Handling |
|---|---|
| Page load timeout | Retry up to `retries`; then fail target with `nav_timeout`. |
| Selector matches nothing | Fail target with `video_not_found`; no artifact. |
| Selector matches a non-media element | Fail with `not_a_media_element`. |
| `.play()` promise rejected (autoplay blocked) | Mute and retry play; if still blocked, fail with `autoplay_blocked`. |
| Media error event / `error_code` set | Fail with `media_error_{code}`. |
| `currentTime` never advances | Fail with `no_playback_progress`. |
| Frames identical (frozen) | Fail with `frozen_frames`. |
| Frames blank/black | Fail with `blank_output`. |
| Video shorter than capture window | Capture available span; verify against actual duration. |
| Lazy-loaded / click-to-play video | Optional pre-capture interaction hook (configurable click target). |
| DRM / encrypted media | Detect and fail fast with `protected_content`; do not attempt circumvention. |
| Disk write failure | Tool-level error, exit code 2. |
| Browser crash | Restart once per target; otherwise exit code 2. |

---

## 7. Tech Stack & Justification

**Recommended:** Python 3.11+ with **Playwright** for browser automation, **Pillow/NumPy** for frame-difference analysis, and **ffmpeg** (invoked only in clip mode).

Rationale:
- **Playwright** offers reliable headless Chromium/Firefox/WebKit control, robust auto-waiting, element-region screenshots, native video recording, and straightforward in-page JS evaluation for reading `HTMLMediaElement` state — the core verification signal.
- **Python** is the lingua franca of QA automation; NumPy makes per-pixel frame deltas trivial and fast, and the dataclass-based config maps cleanly to JSON.
- **ffmpeg** is the de-facto standard for assembling/encoding a short clip when clip mode is requested; it is optional so the default frames mode has no external binary dependency.

**Viable alternative:** Node.js + TypeScript with Playwright. Advantages: Playwright is first-class in Node, page-context JS shares the same language, single-runtime deployment. Choose this if the surrounding test harness is already JS/TS. The module contracts in §4 are language-agnostic.

**Rejected:** Selenium (weaker auto-waiting and media tooling); raw Puppeteer (Chromium-only). Direct HTTP fetching of the video stream is rejected outright — it would not exercise the real player and would blur the line into downloading rather than verifying.

---

## 8. Testing Strategy (for VPV itself)

- **Unit tests:** Verification Engine against synthetic frame sets (moving, frozen, blank) and crafted `MediaSnapshot` pairs covering each verdict branch.
- **Fixture pages:** a local static site with controlled `<video>` cases — playing, autoplay-blocked, broken source, frozen first frame, short clip, lazy-loaded. Run VPV end-to-end against these and assert exit codes/JSON.
- **Contract tests:** config validation (rejects bad schema, unwritable dir).
- **Concurrency tests:** multiple targets, ensuring isolation and correct aggregate exit code.
- **CI gating:** the fixture-page suite runs on every commit; no live external sites in CI.

---

## 9. Compliance & Authorization Notes

- VPV must be run only against sites the operator **owns or is explicitly authorized to test**. Authorization is an operational precondition, not something the tool can assume.
- Respect `robots.txt` and the target site's terms of service; provide a configurable, identifiable User-Agent so operators can be transparent about automated checks.
- Do **not** attempt to access DRM-protected, authenticated, or paywalled content; detect such cases and fail fast rather than circumvent.
- Captured artifacts should be **minimal and short-lived** — they exist only to verify playback. Recommend a retention/cleanup policy (e.g. purge after N days) and storing only what's needed for debugging.
- Rate-limit and stagger checks to avoid burdening target infrastructure.

---

## 10. Open Questions & Assumptions

**Assumptions**
- Targets use standard HTML5 `<video>` elements (not pure canvas/WebGL renderers).
- The operator can supply a stable selector for the target video.
- Local disk is the artifact destination for v1 (no cloud upload in scope).

**Open questions**
1. Should v1 support authenticated sessions (cookie/token injection) for testing logged-in pages, or stay anonymous-only?
2. Is audio verification in scope, or is visual playback sufficient?
3. Preferred result sink beyond stdout/JSON file — webhook, metrics endpoint (e.g. Prometheus), or test-runner reporter?
4. Should frozen/blank thresholds be auto-calibrated per target, or remain static config?
5. Multi-video pages: verify all videos on a page, or only the explicitly selected one?
```