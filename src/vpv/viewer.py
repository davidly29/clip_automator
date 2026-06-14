"""Lightweight, view-only player for captured clips.

Serves the video files under a directory (e.g. ``vpv-artifacts``) as a
full-screen, vertically scroll-snapping feed (purely for viewing — no comments,
likes, or other social features). The UI is a small React SPA (built into
``vpv/web``) backed by a JSON API; if the SPA isn't present, a dependency-free
fallback page is served instead. Uses only the Python standard library at run
time.

Run:  vpv-view --dir ./vpv-artifacts [--port 8000] [--open]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from mimetypes import guess_type
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v", ".ogg", ".ogv"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MEDIA_EXTS = VIDEO_EXTS | IMAGE_EXTS

# Built React SPA (vite build outputs here). Optional — falls back if absent.
WEB_DIR = Path(__file__).resolve().parent / "web"


def find_videos(root: Path) -> list[Path]:
    """All video files under root, newest first."""
    vids = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS]
    return sorted(vids, key=lambda p: p.stat().st_mtime, reverse=True)


def _label(path: Path, root: Path) -> str:
    """A human-friendly caption: the artifact folder name, else the file stem."""
    if path.stem.lower() == "snippet" and path.parent != root:
        return path.parent.name
    return path.stem


def _media_url(path: Path, root: Path) -> str:
    return "/media/" + quote(path.relative_to(root).as_posix())


def api_videos(root: Path) -> list[dict]:
    """Rich list for the SPA: each clip plus its poster frame and verdict."""
    items: list[dict] = []
    for p in find_videos(root):
        d = p.parent
        meta = None
        mp = d / "metadata.json"
        if mp.is_file():
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = None
        poster = None
        frames = sorted(d.glob("frame_*.png"))
        if frames:
            poster = _media_url(frames[0], root)
        verdict = (meta or {}).get("verdict") or {}
        items.append({
            "src": _media_url(p, root),
            "poster": poster,
            "label": _label(p, root),
            "passed": verdict.get("passed"),
            "code": verdict.get("code"),
            "reasons": verdict.get("reasons") or [],
            "captured_at": (meta or {}).get("captured_at"),
        })
    return items


# Shown only if the React SPA hasn't been built into vpv/web.
_NOT_BUILT = (
    "<!doctype html><meta charset='utf-8'>"
    "<body style='font-family:system-ui;background:#0a0a0b;color:#eee;"
    "display:grid;place-items:center;height:100vh;margin:0;text-align:center'>"
    "<div><h2>Viewer UI not built</h2>"
    "<p>Build it with <code>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</code>, "
    "or reinstall the package.<br>The API is live at "
    "<a style='color:#9cf' href='/api/videos'>/api/videos</a>.</p></div></body>"
)


class _Handler(BaseHTTPRequestHandler):
    root: Path = Path(".")

    def log_message(self, fmt, *args):  # quieter than the default
        sys.stderr.write("[vpv-view] " + (fmt % args) + "\n")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/videos":
            self._send_json(api_videos(self.root))
        elif path.startswith("/media/"):
            self._serve_media(path[len("/media/"):])
        else:
            self._serve_app(path)

    # --- responses ---
    def _send_json(self, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, text: str):
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path: Path):
        ctype = guess_type(str(path))[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # --- SPA / fallback ---
    def _serve_app(self, path: str):
        index = WEB_DIR / "index.html"
        if index.is_file():
            rel = path.lstrip("/")
            if rel and rel != "index.html":
                f = (WEB_DIR / rel).resolve()
                if (f == WEB_DIR.resolve() or WEB_DIR.resolve() in f.parents) and f.is_file():
                    self._send_file(f)
                    return
            self._send_file(index)  # SPA entry / client-side fallback
            return
        # SPA not built — show a short message (the JSON API still works).
        if path in ("/", "/index.html"):
            self._send_html(_NOT_BUILT)
        else:
            self.send_error(404, "Not found")

    # --- media (videos + poster frames), with HTTP range support ---
    def _resolve(self, rel_quoted: str) -> Path | None:
        rel = unquote(rel_quoted)
        root = self.root.resolve()
        target = (root / rel).resolve()
        if target != root and root not in target.parents:
            return None  # path traversal attempt
        if not target.is_file() or target.suffix.lower() not in MEDIA_EXTS:
            return None
        return target

    def _serve_media(self, rel_quoted: str):
        target = self._resolve(rel_quoted)
        if target is None:
            self.send_error(404, "Not found")
            return
        ctype = guess_type(str(target))[0] or "application/octet-stream"
        size = target.stat().st_size
        rng = self.headers.get("Range")
        try:
            if not rng:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(size))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                with open(target, "rb") as f:
                    shutil.copyfileobj(f, self.wfile)
                return
            start, end = self._parse_range(rng, size)
            if start is None:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            with open(target, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client seeked/closed; normal for video streaming

    @staticmethod
    def _parse_range(rng: str, size: int):
        if not rng.startswith("bytes="):
            return None, None
        spec = rng[len("bytes="):].split(",")[0].strip()
        start_s, _, end_s = spec.partition("-")
        try:
            if start_s == "":  # suffix range: last N bytes
                start = max(0, size - int(end_s))
                end = size - 1
            else:
                start = int(start_s)
                end = int(end_s) if end_s else size - 1
        except ValueError:
            return None, None
        end = min(end, size - 1)
        if start > end or start >= size:
            return None, None
        return start, end


def serve(directory: Path, host: str, port: int, open_browser: bool) -> int:
    directory = directory.expanduser().resolve()
    if not directory.is_dir():
        sys.stderr.write(f"error: directory not found: {directory}\n")
        return 2
    handler = type("BoundHandler", (_Handler,), {"root": directory})
    httpd = ThreadingHTTPServer((host, port), handler)
    actual_port = httpd.server_address[1]
    url = f"http://{host if host else '127.0.0.1'}:{actual_port}/"
    n = len(find_videos(directory))
    ui = "React UI" if (WEB_DIR / "index.html").is_file() else "fallback UI"
    print(f"VPV viewer ({ui}) serving {n} clip(s) from {directory}")
    print(f"  {url}   (Ctrl+C to stop)")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(
        prog="vpv-view",
        description="View captured clips in a vertical, scroll-snapping feed (view-only).",
    )
    p.add_argument("--dir", type=Path, default=Path("./vpv-artifacts"),
                   help="Folder to scan for videos (default: ./vpv-artifacts).")
    p.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1).")
    p.add_argument("--port", type=int, default=8000, help="Port (default: 8000; 0 = auto).")
    p.add_argument("--open", action="store_true", help="Open the viewer in a browser.")
    args = p.parse_args(argv)
    return serve(args.dir, args.host, args.port, args.open)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
