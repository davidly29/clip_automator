You are a senior software architect. Write a detailed design specification
for a command-line application. Output the spec as a structured document with
the sections listed at the end.

## Purpose
The application is a QA / synthetic-monitoring tool. Its job is to verify that
videos on a given website are loading and playing correctly. As evidence of a
successful check, it captures a short snippet preview (a small set of frames or
a brief clip) of a target video and saves it to a configurable local directory.
The snippet exists purely as proof-of-playback artifact for automated testing;
it is not a media-archiving or redistribution tool.

## Inputs (make all of these configurable)
- Target website domain / page URL
- A selector or identifier for the specific video element on the page
- Local output directory (absolute path), with sensible default
- Snippet parameters: duration or frame count, capture interval, resolution
- Timeout and retry settings

## Core functional requirements
1. Navigate to the target page using headless browser automation.
2. Locate the specified video element and trigger/confirm playback.
3. Capture the snippet — e.g. sample N frames over a few seconds, or grab a
   short clip — as evidence that the video rendered.
4. PLAYBACK VERIFICATION (the key feature): determine whether the video is
   actually playing, not just present. Discuss techniques such as: checking
   HTMLMediaElement properties (readyState, currentTime advancing, paused,
   ended, error), listening for playback events, and confirming captured frames
   differ over time (a frozen/black frame should fail the check).
5. Save the snippet to the configured directory with a structured filename
   (timestamp, domain, video id) and emit a pass/fail result.

## Non-functional requirements
- Robust error handling: page load failure, missing element, codec/format
  issues, network timeouts, autoplay restrictions.
- Logging and a machine-readable result (JSON) suitable for CI pipelines.
- Configurable concurrency for checking multiple videos.
- Cross-platform local file handling.

## Recommended tech stack
Propose a concrete stack and justify it. Consider browser automation
(Playwright / Puppeteer / Selenium), frame/clip extraction (e.g. ffmpeg or
screenshot sampling), and the host language.

## Compliance considerations
Include a short section noting that the tool should respect robots.txt and the
target site's terms of service, should be run only against sites the operator
is authorized to test, and should keep captured artifacts minimal and
short-lived since their only purpose is verification.

## Required output sections
1. Overview & goals
2. Architecture diagram (described in text) and component breakdown
3. Data flow (input → navigation → capture → verification → output)
4. Detailed module specs with key functions/interfaces
5. Configuration schema
6. Error handling & edge cases
7. Tech stack with justification
8. Testing strategy for the tool itself
9. Compliance/authorization notes
10. Open questions / assumptions