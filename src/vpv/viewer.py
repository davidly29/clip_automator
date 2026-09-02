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
import os
import re
import shutil
import subprocess
import sys
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from mimetypes import guess_type
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from .auth import COOKIE_NAME, SESSION_TTL_S, AuthSettings, parse_cookies
from .errors import ConfigError
from .jobs import CONTAINER_BROWSER_ARGS, JobManager

VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v", ".ogg", ".ogv"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MEDIA_EXTS = VIDEO_EXTS | IMAGE_EXTS

# Files dragged in from the desktop land here (a subfolder of the served dir).
UPLOAD_DIR_NAME = "uploads"
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024  # 1 GiB ceiling per file

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
            "signals": verdict.get("signals") or {},
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


def _safe_upload_name(raw: str) -> str | None:
    """Sanitise an uploaded filename: strip any path, require a video extension."""
    name = Path(raw or "").name.strip()
    if not name or Path(name).suffix.lower() not in VIDEO_EXTS:
        return None
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name or None


def _unique_path(p: Path) -> Path:
    """Return p, or p-1/p-2/... if it already exists (never clobber)."""
    if not p.exists():
        return p
    i = 1
    while True:
        cand = p.with_name(f"{p.stem}-{i}{p.suffix}")
        if not cand.exists():
            return cand
        i += 1


class ComposeError(RuntimeError):
    """Raised when ffmpeg fails to combine clips."""


def _ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _has_audio(ffmpeg: str, path: Path) -> bool:
    """True if the file has an audio stream (parsed from ffmpeg's probe output)."""
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)],
        capture_output=True, text=True,
    )
    return "Audio:" in (proc.stderr or "")


def _compose_filter(n: int, audio_flags: list[bool], height: int = 720) -> tuple[str, str | None]:
    """Build (filter_complex, audio_map) to stack n clips side by side.

    Each clip is scaled to a common height; clips that carry audio are mixed.
    Returns the audio map (``"[a]"`` or ``"<idx>:a"``) or None if no clip has sound.
    """
    parts: list[str] = []
    for i in range(n):
        label = "v" if n == 1 else f"v{i}"
        parts.append(f"[{i}:v]scale=-2:{height},setsar=1[{label}]")
    if n > 1:
        ins = "".join(f"[v{i}]" for i in range(n))
        parts.append(f"{ins}hstack=inputs={n}[v]")

    audio_idx = [i for i, a in enumerate(audio_flags) if a]
    if len(audio_idx) == 1:
        return ";".join(parts), f"{audio_idx[0]}:a"
    if len(audio_idx) >= 2:
        ins = "".join(f"[{i}:a]" for i in audio_idx)
        parts.append(f"{ins}amix=inputs={len(audio_idx)}:normalize=0[a]")
        return ";".join(parts), "[a]"
    return ";".join(parts), None


def compose(root: Path, inputs: list[Path], height: int = 720) -> Path:
    """Render 1-3 clips into a single side-by-side video (with sound) under root."""
    ffmpeg = _ffmpeg()
    audio_flags = [_has_audio(ffmpeg, p) for p in inputs]
    filt, amap = _compose_filter(len(inputs), audio_flags, height)
    out = root / f"compose-{datetime.now():%Y%m%d-%H%M%S}.mp4"

    cmd = [ffmpeg, "-y"]
    for p in inputs:
        cmd += ["-i", str(p)]
    cmd += ["-filter_complex", filt, "-map", "[v]"]
    if amap:
        cmd += ["-map", amap, "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
            "-movflags", "+faststart", str(out)]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not out.is_file():
        raise ComposeError((proc.stderr or "ffmpeg failed").strip()[-800:])
    return out


class _Handler(BaseHTTPRequestHandler):
    root: Path = Path(".")
    auth: AuthSettings | None = None
    jobs: JobManager | None = None

    def log_message(self, fmt, *args):  # quieter than the default
        sys.stderr.write("[vpv-view] " + (fmt % args) + "\n")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/healthz":                      # public: Railway health check
            self._send_json({"ok": True})
            return
        if path == "/api/me":                        # public: drives the login screen
            self._me()
            return
        # Data endpoints (API + media) require a session; the static SPA shell
        # is public (it holds no data and renders a login screen itself).
        if (path.startswith("/api/") or path.startswith("/media/")) and not self._is_authed():
            self._send_json({"error": "unauthorized"}, status=401)
            return
        if path == "/api/videos":
            self._send_json(api_videos(self.root))
        elif path == "/api/runs":
            self._runs_list()
        elif path.startswith("/api/runs/"):
            self._run_get(unquote(path[len("/api/runs/"):]))
        elif path.startswith("/media/"):
            self._serve_media(path[len("/media/"):])
        elif path.startswith("/api/"):
            self.send_error(404, "Not found")
        else:
            self._serve_app(path)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/login":                     # public
            self._login()
            return
        if not self._is_authed():
            self._send_json({"error": "unauthorized"}, status=401)
            return
        if path == "/api/logout":
            self._logout()
        elif path == "/api/run":
            self._run_start()
        elif path == "/api/compose":
            self._compose()
        elif path == "/api/upload":
            self._upload()
        else:
            self.send_error(404, "Not found")

    # --- auth ---
    def _auth_on(self) -> bool:
        return bool(self.auth and self.auth.enabled)

    def _is_authed(self) -> bool:
        if not self._auth_on():
            return True
        cookies = parse_cookies(self.headers.get("Cookie"))
        return self.auth.verify_session(cookies.get(COOKIE_NAME)) is not None

    def _me(self):
        default_user = self.auth.user if self.auth else "admin"
        if not self._auth_on():
            self._send_json({"user": default_user, "authRequired": False})
            return
        cookies = parse_cookies(self.headers.get("Cookie"))
        user = self.auth.verify_session(cookies.get(COOKIE_NAME))
        if user is None:
            self._send_json({"authRequired": True, "error": "unauthorized"}, status=401)
            return
        self._send_json({"user": user, "authRequired": True})

    def _login(self):
        body = self._read_json()
        if body is None:
            return
        user = str(body.get("username", ""))
        pw = str(body.get("password", ""))
        if not (self.auth and self.auth.check(user, pw)):
            self._send_json({"error": "invalid credentials"}, status=401)
            return
        token = self.auth.make_session(user or self.auth.user)
        cookie = (f"{COOKIE_NAME}={token}; HttpOnly; SameSite=Lax; Path=/; "
                  f"Max-Age={SESSION_TTL_S}")
        self._send_json({"user": user or self.auth.user}, set_cookie=cookie)

    def _logout(self):
        cookie = f"{COOKIE_NAME}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"
        self._send_json({"ok": True}, set_cookie=cookie)

    # --- verification runs ---
    def _run_start(self):
        body = self._read_json()
        if body is None:
            return
        if self.jobs is None:
            self._send_json({"error": "runs are not available"}, status=503)
            return
        try:
            job = self.jobs.submit(body)
        except ConfigError as e:
            self._send_json({"error": str(e)}, status=400)
            return
        self._send_json(job.to_dict(), status=202)

    def _runs_list(self):
        jobs = self.jobs.list() if self.jobs else []
        self._send_json([j.to_dict() for j in jobs])

    def _run_get(self, job_id: str):
        job = self.jobs.get(job_id) if self.jobs else None
        if job is None:
            self._send_json({"error": "not found"}, status=404)
            return
        self._send_json(job.to_dict())

    def _read_json(self) -> dict | None:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "invalid JSON"}, status=400)
            return None
        if not isinstance(data, dict):
            self._send_json({"error": "expected a JSON object"}, status=400)
            return None
        return data

    # --- upload (drag files in from the desktop) ---
    def _upload(self):
        qs = parse_qs(urlparse(self.path).query)
        name = _safe_upload_name((qs.get("name") or [""])[0])
        if name is None:
            self._send_json({"error": "unsupported or invalid filename"}, status=400)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._send_json({"error": "empty upload"}, status=400)
            return
        if length > MAX_UPLOAD_BYTES:
            self._send_json({"error": "file too large"}, status=413)
            return
        dest_dir = self.root / UPLOAD_DIR_NAME
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = _unique_path(dest_dir / name)
        remaining = length
        try:
            with open(dest, "wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
        except OSError as e:
            dest.unlink(missing_ok=True)
            self._send_json({"error": f"could not save: {e}"}, status=500)
            return
        self._send_json({
            "src": _media_url(dest, self.root),
            "poster": None,
            "label": dest.stem,
            "passed": None,
            "code": None,
            "reasons": [],
            "captured_at": None,
        })

    # --- compose (combine 1-3 clips side by side) ---
    def _compose(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "invalid JSON"}, status=400)
            return
        rels = body.get("clips")
        if not isinstance(rels, list) or not (1 <= len(rels) <= 3):
            self._send_json({"error": "provide 1-3 clips"}, status=400)
            return
        inputs: list[Path] = []
        for r in rels:
            rel = r[len("/media/"):] if isinstance(r, str) and r.startswith("/media/") else r
            target = self._resolve(rel) if isinstance(rel, str) else None
            if target is None:
                self._send_json({"error": f"clip not found: {r}"}, status=400)
                return
            inputs.append(target)
        try:
            out = compose(self.root, inputs)
        except ComposeError as e:
            self._send_json({"error": str(e)}, status=500)
            return
        self._send_json({"src": _media_url(out, self.root)})

    # --- responses ---
    def _send_json(self, obj, status: int = 200, set_cookie: str | None = None):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
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
    if directory.exists() and not directory.is_dir():
        sys.stderr.write(f"error: not a directory: {directory}\n")
        return 2
    directory.mkdir(parents=True, exist_ok=True)  # create on first run (e.g. an empty volume)

    auth = AuthSettings.from_env()
    # In-container capture needs software rendering + no sandbox (runs as root).
    extra_args = CONTAINER_BROWSER_ARGS if os.environ.get("VPV_IN_CONTAINER") else ()
    jobs = JobManager(directory, extra_browser_args=extra_args)
    handler = type("BoundHandler", (_Handler,),
                   {"root": directory, "auth": auth, "jobs": jobs})
    httpd = ThreadingHTTPServer((host, port), handler)
    actual_port = httpd.server_address[1]
    url = f"http://{host if host else '127.0.0.1'}:{actual_port}/"
    n = len(find_videos(directory))
    ui = "React UI" if (WEB_DIR / "index.html").is_file() else "fallback UI"
    print(f"VPV viewer ({ui}) serving {n} clip(s) from {directory}")
    if auth.enabled:
        print(f"  auth: ENABLED (user '{auth.user}')")
    else:
        print("  auth: DISABLED (set VPV_ADMIN_PASSWORD to require login)")
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


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Populate os.environ from a local .env (KEY=VALUE per line) if present.

    Existing environment variables win, so a deployment's real env is never
    overridden. Used for local admin credentials that must not be committed.
    """
    if not path.is_file():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            key, _, val = s.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _load_dotenv()
    # Defaults come from the environment so the same entrypoint works locally and
    # on hosts like Railway (which inject PORT and, for us, HOST/VPV_VIEW_DIR).
    default_dir = os.environ.get("VPV_VIEW_DIR", "./vpv-artifacts")
    default_host = os.environ.get("HOST", "127.0.0.1")
    default_port = int(os.environ.get("PORT", "8000"))
    p = argparse.ArgumentParser(
        prog="vpv-view",
        description="VPV control panel: run verification checks, view clips, and "
                    "compose side-by-side videos (login required when "
                    "VPV_ADMIN_PASSWORD is set).",
    )
    p.add_argument("--dir", type=Path, default=Path(default_dir),
                   help="Folder to scan for videos (default: $VPV_VIEW_DIR or ./vpv-artifacts).")
    p.add_argument("--host", default=default_host,
                   help="Host to bind (default: $HOST or 127.0.0.1).")
    p.add_argument("--port", type=int, default=default_port,
                   help="Port (default: $PORT or 8000; 0 = auto).")
    p.add_argument("--open", action="store_true", help="Open the viewer in a browser.")
    args = p.parse_args(argv)
    return serve(args.dir, args.host, args.port, args.open)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
