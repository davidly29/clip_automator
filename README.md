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

End-to-end: set up → capture a clip → view it. Requires Python 3.10+ (Node is
only needed if you want to modify the viewer UI).

**1. Set up (one-time).** Run from the project root, then activate the venv in
every new terminal so `python` / `vpv` / `vpv-view` resolve to it.

Windows (PowerShell):

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python -m playwright install chromium
```

macOS / Linux:

```bash
python3.10 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m playwright install chromium
```

> Seeing `No module named playwright`? You're not in the venv — activate it, or
> call the venv directly, e.g. `.\.venv\Scripts\vpv.exe ...` (Windows) /
> `.venv/bin/vpv ...` (macOS / Linux).

**2. Capture a clip.** Point VPV at a video page; it plays the video and records
a real `.mp4`:

```powershell
vpv --url "https://example.com/watch/123" --video-selector "video" --play-selector ".play-button" --mode clip --headed --output-dir ./vpv-artifacts
```

It writes a pass/fail JSON result to stdout plus `snippet.mp4` + frames +
`metadata.json` under `./vpv-artifacts/`, and exits `0` (pass) / `1` (fail) /
`2` (error). Use `--headed` if a headless capture comes out black.

For real sites (search bars, age gates, pre-roll ads, custom players), save the
settings once as a **profile** and just reuse it:

```powershell
# copy profiles/example.properties to profiles/<yoursite>.properties, edit it, then:
vpv --profile <yoursite>
```

**3. View the captured clips** in a vertical, TikTok-style scrolling feed:

```powershell
vpv-view --dir ./vpv-artifacts --open
```

**Run the tests** (optional):

```bash
pip install -e ".[test]"
pytest
```

Full options are in [Usage](#usage); per-site setup is in
[Profiles](#profiles-one-file-per-website).

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

## Usage

> Setup (venv, install, browser) is in the [Quickstart](#quickstart). ffmpeg is
> bundled via `imageio-ffmpeg`, so `clip` mode works with no extra install.

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

### Profiles (one file per website)

Instead of re-typing flags, save a site's settings as a **profile** and invoke
it by name. Profiles use a simple `key = value` properties format (see
[`profiles/example.properties`](./profiles/example.properties)):

```properties
# profiles/mysite.properties
url = https://mysite.example/
dismiss_id = disclaimer-over18btn, disclaimer-accept_cookies
random_selector = div[id^=video_]
skip_ad_selector = text=Skip ad
video_selector = video
play_id = anc-tst-play-btn
fullscreen_selector = span.fullscreen
mode = clip
headless = false
output_dir = ./vpv-artifacts
```

```bash
vpv --profile mysite
```

- Keys mirror the CLI/config names; list values are comma-separated; booleans are
  `true`/`false`. CLI flags still override a profile (`vpv --profile mysite --mode frames`).
- Profiles are looked up (by `<name>` with `.properties`/`.jsonc`/`.json`) in:
  `--profiles-dir <dir>`, then `$VPV_PROFILES_DIR`, then `./profiles`, then
  `~/.vpv/profiles`. A path also works: `--profile ./path/to/site.properties`.
- `--config <file>` also accepts `.properties` (single-target) in addition to
  JSON/JSONC.

#### Adding a profile for a new site

1. **Copy the template:** `cp profiles/example.properties profiles/<yoursite>.properties`.
2. **Inspect the page in DevTools** and fill in the selectors below. The steps
   run in this order, and each is optional — set only what the site needs:

   | Step | Profile key(s) | Notes |
   | --- | --- | --- |
   | Page to open | `url` | Required. |
   | Dismiss overlays | `dismiss_id` / `dismiss_selector` | Age gate, cookies, … Comma-separate several; clicked in order. Any element (e.g. a `<span>`), not just buttons. |
   | Find a video | `random_selector` *or* `random_ids` *or* `result_selector` (+ `search_id`/`search_query`) | `random_selector` picks one match at random (e.g. `div[id^=video_]`). |
   | The video element | `video_selector` | The `<video>` to verify. |
   | Start playback | `play_id` / `play_selector` | Clicked **first** — on many sites this is what starts the pre-roll ad. |
   | Skip the ad | `skip_ad_selector`, `ad_timeout_s` | e.g. `text=Skip ad`. VPV waits for it to become clickable. |
   | Go fullscreen | `fullscreen_selector` (a button to click) *or* `fullscreen_id`/`fullscreen_target` (element to fullscreen) | |
   | Capture | `mode` (`clip`/`frames`), `duration_s`, `frame_count` | `clip` records a real `.mp4` of `duration_s` seconds. |
   | Run options | `headless` (use `false` so video isn't black), `output_dir`, `browser_args` | |

3. **Comments must be on their own line** (a line starting with `#` or `!`).
   Inline comments after a value are kept verbatim, because `#` is a valid value
   character (e.g. a CSS id like `#anc-tst-play-btn`).
4. **Run it:** `vpv --profile <yoursite>`. Check the JSON result for
   `selected_video`, `ad_skipped`, and `fullscreen` to confirm each step fired;
   tweak the matching selector if one didn't.

The flow VPV executes for each target:

```
open url -> dismiss overlays -> (search/result | random pick) -> locate <video>
         -> click play (starts ad) -> skip ad -> go fullscreen
         -> confirm playing -> capture -> verify -> save artifact
```

### Interaction flow parameters

| Parameter | CLI flag | Config key | Meaning |
| --- | --- | --- | --- |
| Dismiss overlay (by id) | `--dismiss-id` (repeatable) | `dismiss_id` | HTML id of an element to click — age gate, cookies… (=> `#id`). |
| Dismiss overlay (by CSS) | `--dismiss-selector` (repeatable) | `dismiss_selector` | CSS selector to click. Clicks any element, not just buttons. |
| Auto-dismiss consent | `--auto-dismiss` / `--no-auto-dismiss` | `auto_dismiss_consent` | Heuristic accept-button clicker (default **on**; only if no explicit dismiss). |
| Accept-button phrases | `--consent-text` (repeatable) | `consent_texts` | Override the phrases the heuristic matches. |
| Search bar (by id) | `--search-id` | `search_id` | HTML id, turned into `#id`. |
| Search bar (by CSS) | `--search-selector` | `search_selector` | Any CSS selector (wins over id). |
| Search text | `--search-query` | `search_query` | Text typed into the search bar. |
| Submit button | `--search-submit` | `search_submit` | Optional; omit to press Enter. |
| Result to open | `--result-selector` | `result_selector` | Element clicked to open the video. |
| Random video IDs | `--random-id` (repeatable) | `random_ids` | Candidate ids; one present id is picked at random and clicked. |
| Random by selector | `--random-selector` | `random_selector` | CSS selector; one match is picked at random (e.g. `div[id^=video_]`). |
| Random seed | `--random-seed` | `random_seed` | Makes the random pick reproducible. |
| Skip pre-roll ad | `--skip-ad-selector` | `skip_ad_selector` | Element to click to skip an ad (e.g. `text=Skip ad`); waits for it. |
| Ad wait timeout | `--ad-timeout` | `ad_timeout_s` | Max seconds to wait for the skip control (default 15). |
| Video element | `--video-selector` | `video_selector` | The `<video>` to verify. |
| Play button | `--play-selector` / `--play-id` | `play_selector` / `play_id` | Optional play control (id => `#id`). |
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

- **Explicit element(s):** for age gates or modals that aren't standard buttons
  (e.g. a `<span>` "I am 18 or older"), point VPV at them directly. The flags are
  **repeatable**, so you can clear several overlays in order (age gate, then
  cookies), and they click *any* element, not just `<button>`s:

  ```bash
  vpv --url ... --dismiss-id disclaimer-over18btn --dismiss-id disclaimer-accept_cookies
  ```

  When any explicit dismiss is given, it's used and the heuristic is skipped.

All are best-effort: a page with no overlay just proceeds.

### Pre-roll ads

If a video plays a skippable ad first, point VPV at the skip control and it will
wait for it to become clickable (ads usually enforce a few seconds), click it,
then continue to the real video:

```bash
vpv --url ... --skip-ad-selector "text=Skip ad" --ad-timeout 15
```

`text=Skip ad` is a Playwright text selector (whitespace-trimmed); a CSS
selector or id works too. The result reports `"ad_skipped": true|false`.

### Random video selection

Give VPV a set of candidate video element IDs and it will land on the homepage,
filter to the ones actually present, and click one at random to open it:

```bash
vpv --url "https://videosite.example/" \
    --random-id featured-1 --random-id featured-2 --random-id featured-3 \
    --random-seed 42 \           # optional: reproducible pick
    --video-selector "video" --output-dir ./vpv-artifacts
```

When videos don't have stable IDs but share a structure (e.g. each is a
`<div id="video_…">` or an `<a>` inside `div.mb.hdy`), use a **selector** instead
and VPV picks one match at random:

```bash
vpv --url "https://videosite.example/" \
    --random-selector "div[id^=video_]" \
    --video-selector "video" --output-dir ./vpv-artifacts
```

The chosen element is reported as `selected_video` in the JSON result and in the
artifact's `metadata.json`. If nothing matches, the target fails with
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

> **Captured media is video-only — no audio.** Playwright's recording doesn't
> include sound, and playback is muted to satisfy autoplay. `metadata.json`
> records `"audio_captured": false` to make this explicit.

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

## Viewing captured clips

A bundled, view-only player renders the clips in a folder as a full-screen,
vertically scroll-snapping feed (TikTok-style — no comments, likes, or other
social features; just the videos):

```bash
vpv-view --dir ./vpv-artifacts --open
```

Then scroll (or use ↑/↓ / space) to move between clips. The clip in view
autoplays muted and loops; tap a video to pause/resume; the toolbar has a
sound toggle, a **grid ⇄ feed** switch, and **pass / fail** filters. Each clip
shows a poster thumbnail (from the captured `frame_000.png`) and its verdict
badge (from `metadata.json`). Options: `--port` (default 8000), `--host`,
`--open`. Also runs as `python -m vpv.viewer`.

The UI is a small **React (Vite)** SPA in [`frontend/`](./frontend) backed by a
JSON API (`GET /api/videos`) and range-streamed `/media/...` from the Python
server (stdlib only at run time). The built app is committed to
`src/vpv/web/`, so `vpv-view` works out of the box with no Node required.

To change the UI:

```bash
cd frontend
npm install
npm run dev      # hot-reload dev server (proxies /api and /media to :8000)
# in another terminal, run the backend it proxies to:
#   vpv-view --dir ./vpv-artifacts --port 8000
npm run build    # rebuild the shipped app into ../src/vpv/web
```

If `src/vpv/web/` is absent, `vpv-view` falls back to a dependency-free page.

Notes:
- **Aspect ratio:** clips are shown with `object-fit: contain`, so landscape
  (e.g. 1080p) videos letterbox in the vertical feed rather than being cropped.
- **npm advisories:** `npm install` may report advisories in the frontend's
  **dev-only** tooling (Vite/esbuild dev server). These don't affect the shipped
  viewer in `src/vpv/web/` or `vpv-view` at run time (no Node is used to run it).

## Deploy the viewer (Docker / Railway)

The **viewer** (`vpv-view`) is the deployable web service — it serves the SPA,
streams clips, and composes side-by-side videos. (The `vpv` verifier is a local
browser-automation CLI and is not part of the deployed image.)

The [`Dockerfile`](./Dockerfile) is a two-stage build: Node builds the SPA, then
a slim Python image installs the package and runs `vpv-view`. No Playwright
browsers or system ffmpeg are needed — the viewer only uses the ffmpeg bundled
by `imageio-ffmpeg`. The entrypoint reads `$PORT`, `$HOST`, and `$VPV_VIEW_DIR`
from the environment (the image defaults `HOST=0.0.0.0`, `VPV_VIEW_DIR=/data`).

Build and run locally:

```bash
docker build -t vpv-viewer .
docker run --rm -p 8000:8000 -e PORT=8000 -v "$PWD/vpv-artifacts:/data" vpv-viewer
# open http://localhost:8000
```

**Railway:** the repo includes [`railway.json`](./railway.json) pinning the
Dockerfile builder and a `/` health check. Create a service from this repo —
Railway injects `$PORT` automatically, so no config is required to boot. To keep
uploaded/rendered clips across deploys, attach a **Volume** mounted at `/data`
(otherwise `/data` is ephemeral, which is fine for a stateless demo).

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
  cli.py           Entry point (vpv)
  viewer.py        Vertical clip viewer server + JSON API (vpv-view)
  web/             Built React viewer SPA (served by vpv-view)
frontend/          React (Vite) source for the viewer UI
```
