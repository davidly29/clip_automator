# Video Playback Verifier (VPV)

A QA / synthetic-monitoring CLI that confirms videos on a web page **actually
play** — not merely exist in the DOM — and captures a short proof-of-playback
snippet as evidence. It emits a machine-readable JSON result and a meaningful
exit code for CI pipelines.

This is an implementation of the design in
[`video-playback-verifier-design-spec.md`](./video-playback-verifier-design-spec.md).

> VPV is **not** a media downloader or archiver. Snippets are minimal,
> short-lived verification artifacts. Run it only against sites you own or are
> authorized to test. See [Compliance](#compliance).

## Quickstart

Requires Python 3.10+. **Windows (PowerShell):**

```powershell
# 1. Create an isolated environment with Python 3.10 (from the project root)
py -3.10 -m venv .venv

# 2. Activate it — now python / pip / vpv resolve to THIS venv, not your
#    system Python. Your prompt should show (.venv).
.\.venv\Scripts\Activate.ps1

# 3. Install VPV + download the headless browser (one-time)
pip install -e .
python -m playwright install chromium

# 4. Verify a video and capture proof of playback
vpv --url https://www.youtube.com/ --output-dir ./vpv-artifacts
```

**macOS / Linux:**

```bash
python3.10 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m playwright install chromium
vpv --url https://www.youtube.com/ --output-dir ./vpv-artifacts
```

That prints a JSON result to stdout, writes frames + `metadata.json` to
`./vpv-artifacts/`, and exits `0` (pass) / `1` (fail) / `2` (tool error).

**Real-world example** — verify a specific YouTube video and record an actual
`.mp4` clip (PowerShell; note the trailing backticks must be the *last*
character on each line, with no trailing space):

```powershell
vpv --url "https://www.youtube.com/watch?v=8ToP_EnAlLU" `
    --video-selector "video.html5-main-video" `
    --play-selector ".ytp-play-button" `
    --mode clip --output-dir ./vpv-artifacts
```

The same command on one line (safest for copy-paste):

```powershell
vpv --url "https://www.youtube.com/watch?v=8ToP_EnAlLU" --video-selector "video.html5-main-video" --play-selector ".ytp-play-button" --fullscreen-id player-full-bleed-container --mode clip --output-dir ./vpv-artifacts
```

In `--mode clip` the video is brought to fullscreen before capture and the
artifact folder gets a real `snippet.mp4` (plus the sampled frames and
`metadata.json`). Some players (YouTube included) go fullscreen on a **container**
rather than the `<video>` itself, so point `--fullscreen-id` at that element
(e.g. `player-full-bleed-container`). The result JSON / `metadata.json` reports
`"fullscreen": true|false` so you can confirm it engaged.

> **`No module named playwright`?** You're running a Python that isn't the venv
> (a bare `python` often points at a global install). Either **activate** the
> venv (step 2), or call the venv interpreter directly:
> `.\.venv\Scripts\python.exe -m vpv ...` (Windows) /
> `.venv/bin/python -m vpv ...` (macOS / Linux).

**Drive a video site** (search → open a result → play → record a real `.mp4`):

```bash
vpv --url "https://www.youtube.com/" \
    --search-id "search" --search-query "nature documentary" \
    --result-selector ".result a" --video-selector "video" \
    --play-selector ".play-btn" --mode clip \
    --output-dir ./vpv-artifacts
```

**Random pick** (open one of several homepage videos at random):

```bash
vpv --url "https://videosite.example/" \
    --random-id featured-1 --random-id featured-2 --random-id featured-3 \
    --video-selector "video" --output-dir ./vpv-artifacts
```

> Consent / cookie banners are dismissed automatically (see
> [Consent / first-visit modals](#consent--first-visit-modals)) — no extra flags
> needed for the common cases.

**Run the tests:**

```bash
pip install -e ".[test]"
pytest
```

More detail in [Usage](#usage) below.

## How it works

```
navigate -> [dismiss consent] -> [search -> open result | random pick]
         -> locate <video> -> [click play/fullscreen]
         -> play -> capture -> verify -> persist -> report
```

The bracketed steps are an optional, fully **parameterized interaction flow**:
you tell VPV the website URL, how to clear a consent/first-visit modal, the
search bar, what to search for, which result to open (or a set of video IDs to
pick from at random), and where the play/fullscreen controls are — and it drives
the site to reach a playing video before capturing.

Playback is verified by combining two independent signal sources:

1. **HTMLMediaElement state** — `currentTime` advancing, `readyState`, `paused`,
   `ended`, and `MediaError.code`, read via in-page JS.
2. **Frame-difference analysis** — captured frames must change over time and not
   be uniformly black/blank, so a video that *reports* playing but renders a
   frozen or black frame still fails.

A target **passes** only when time advanced **and** there was no media error
**and** the frames showed motion.

## Install

Requires Python 3.10+.

```bash
pip install -e .
python -m playwright install chromium   # one-time browser download
# ffmpeg is bundled via the imageio-ffmpeg dependency, so clip mode works
# out of the box; a system ffmpeg on PATH is used if present.
```

## Usage

Simple case — the page already has a `<video>`:

```bash
vpv --url https://example.com/watch/123 \
    --video-selector "video" \
    --output-dir ./vpv-artifacts
```

Parameterized, search-driven case — drive a video site to the clip you want and
record an **actual video**:

```bash
vpv --url "https://videosite.example/" \
    --search-id "search" \              # HTML id of the search bar (=> #search)
    --search-query "nature documentary" \
    --search-submit "button.search-go" \  # optional; omit to press Enter
    --result-selector ".result a" \      # result to click to open the video
    --video-selector "video" \
    --play-selector ".vjs-play-control" \
    --fullscreen-selector ".vjs-fullscreen-control" \
    --mode clip \                        # record a real .mp4 (default: frames)
    --output-dir ./vpv-artifacts
```

Multiple targets / full control via a config file (see
[`vpv.config.example.jsonc`](./vpv.config.example.jsonc)):

```bash
vpv --config vpv.config.example.jsonc
```

### Interaction flow parameters

| Parameter | CLI flag | Config key | Meaning |
| --- | --- | --- | --- |
| Consent OK button (by id) | `--dismiss-id` | `dismiss_id` | HTML id of an OK/Accept button, turned into `#id`. |
| Consent OK button (by CSS) | `--dismiss-selector` | `dismiss_selector` | Any CSS selector (wins over id). |
| Auto-dismiss consent | `--auto-dismiss` / `--no-auto-dismiss` | `auto_dismiss_consent` | Heuristic accept-button clicker (default **on**). |
| Accept-button phrases | `--consent-text` (repeatable) | `consent_texts` | Override the phrases the heuristic matches. |
| Search bar (by id) | `--search-id` | `search_id` | HTML id, turned into `#id`. |
| Search bar (by CSS) | `--search-selector` | `search_selector` | Any CSS selector (wins over id). |
| Search text | `--search-query` | `search_query` | Text typed into the search bar. |
| Submit button | `--search-submit` | `search_submit` | Optional; omit to press Enter. |
| Result to open | `--result-selector` | `result_selector` | Element clicked to open the video. |
| Random video IDs | `--random-id` (repeatable) | `random_ids` | Candidate ids; one is picked at random and clicked. |
| Random seed | `--random-seed` | `random_seed` | Makes the random pick reproducible. |
| Video element | `--video-selector` | `video_selector` | The `<video>` to verify. |
| Play button | `--play-selector` | `play_selector` | Optional play control. |
| Fullscreen button | `--fullscreen-selector` | `fullscreen_selector` | Optional site fullscreen control to click. |
| Fullscreen element | `--fullscreen-id` / `--fullscreen-target` | `fullscreen_id` / `fullscreen_target` | Element to make fullscreen (default: the video). |
| Auto fullscreen | `--fullscreen` / `--no-fullscreen` | `fullscreen` | Put the video fullscreen before capture (default **on**). |

All interaction parameters are optional; with none set, VPV just verifies a
`<video>` already on the page. CLI flags override config-file values, which
override built-in defaults. Run `vpv --help` for everything; you can also invoke
it as `python -m vpv ...`.

### Consent / first-visit modals

Many sites show a cookie/consent banner or a first-visit modal that must be
accepted before anything is clickable. VPV handles this in two ways:

- **Auto-dismiss (recommended, default on, zero config):** before interacting,
  VPV clicks well-known consent buttons (OneTrust, Google Funding Choices, etc.)
  and any button whose accessible name matches a common phrase — `Accept`,
  `Accept all`, `Agree`, `OK`, `Got it`, `Allow`, `Consent` — searched across the
  page **and its iframes** (consent UIs are often framed). This scales across
  sites without per-site selectors. Disable with `--no-auto-dismiss`.
- **Custom phrases:** if a site uses different wording (e.g. another language, or
  `Enter site`), override the matched phrase list — this *replaces* the built-in
  list:

  ```bash
  vpv --url ... --consent-text "Enter site" --consent-text "Zustimmen"
  # or in a config file:  "consent_texts": ["Enter site", "Zustimmen"]
  ```

- **Explicit button:** for unusual modals, point VPV straight at the button with
  `--dismiss-id <id>` or `--dismiss-selector <css>`. This is tried first, then
  auto-dismiss as a fallback.

All are best-effort: a page with no banner just proceeds.

### Random video selection

Give VPV a set of candidate video element IDs and it will land on the homepage,
filter to the ones actually present, and click one at random to open it:

```bash
vpv --url "https://videosite.example/" \
    --random-id featured-1 --random-id featured-2 --random-id featured-3 \
    --random-seed 42 \           # optional: reproducible pick
    --video-selector "video" --output-dir ./vpv-artifacts
```

The chosen element is reported as `selected_video` in the JSON result and in the
artifact's `metadata.json`. If none of the IDs are present, the target fails with
`video_not_found`.

### Capture modes

- `frames` (default): samples screenshots of the video region — fast,
  dependency-free, sufficient for verification. By default it captures **2**
  screenshots (one near the start, one near the end of the window — enough to
  detect motion vs a frozen frame). Override with `--frame-count N`, or set
  `"frame_count": null` in a config file to sample every `interval_ms`.
- `clip`: records an **actual playable video** of the session via Playwright and
  transcodes it to `snippet.mp4` with the bundled ffmpeg. The clip is trimmed to
  begin once the video is **fullscreen** (Playwright can't pause/resume
  recording, so the pre-fullscreen navigation is cut off). Frames are still
  sampled for the verification signal.

### Output

The aggregate result is printed to **stdout** as JSON (logs go to stderr):

```json
{
  "run_id": "1a2b3c4d",
  "summary": { "total": 1, "passed": 1, "failed": 0, "exit_code": 0, "tool_error": null },
  "targets": [
    {
      "url": "https://example.com/watch/123",
      "passed": true,
      "code": "pass",
      "reasons": ["currentTime advanced 1.98s (>= 0.25s)", "frames moving (motion 41.2)"],
      "signals": { "time_advance_s": 1.98, "motion_score": 41.2, "blank": false },
      "artifact": { "directory": ".../20260614-093000_example.com_idx0", "...": "..." }
    }
  ]
}
```

**Exit codes:** `0` all passed · `1` at least one failed · `2` tool/runtime
error (invalid config, browser crash, disk failure).

### Artifacts

Each target writes a directory named
`{YYYYMMDD-HHMMSS}_{domain}_{video-id}/` containing the sampled
`frame_NNN.png` files (and `snippet.mp4` in clip mode), plus a
`metadata.json` sidecar with the verdict, media snapshots, and the config used.

## Failure codes

`nav_timeout`, `video_not_found`, `not_a_media_element`, `autoplay_blocked`,
`media_error`, `no_playback_progress`, `frozen_frames`, `blank_output`,
`protected_content`, `browser_error`, `tool_error`.

## Development & tests

```bash
pip install -e ".[test]"
pytest                       # pure-logic tests run anywhere
python -m playwright install chromium
pytest                       # now the end-to-end fixture tests run too
```

The pure-logic suite (`test_verify.py`, `test_config.py`, `test_artifact.py`)
needs no browser. `test_e2e.py` serves the pages in `tests/fixtures/` over a
local HTTP server and drives a real headless Chromium; it self-skips if the
browser isn't installed.

## Compliance

- Run only against sites you **own or are explicitly authorized to test**.
- Respect `robots.txt` and the site's terms of service; set an identifiable
  `user_agent`.
- VPV does **not** bypass DRM, auth, or paywalls — it detects encrypted media
  and fails fast (`protected_content`).
- Keep artifacts minimal and short-lived; purge them on a retention schedule.

## Project layout

```
src/vpv/
  config.py        Config Loader      (§4.1)
  browser.py       Browser Controller (§4.2)
  capture.py       Capture Engine     (§4.3)
  verify.py        Verification Engine(§4.4)
  artifact.py      Artifact Writer    (§4.5)
  reporter.py      Result Reporter    (§4.6)
  orchestrator.py  Orchestrator
  logging_setup.py Structured Logger
  cli.py           Entry point
```
