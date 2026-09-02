"""HTTP-level tests for auth gating and the run API (src/vpv/viewer.py)."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from vpv import viewer
from vpv.auth import AuthSettings
from vpv.jobs import JobManager


def _pass_result(_p):
    r = SimpleNamespace(passed=True, code=SimpleNamespace(value="pass"),
                        reasons=[], signals={}, error=None, artifact=None)
    return SimpleNamespace(results=[r], tool_error=None)


@pytest.fixture
def server(tmp_path):
    (tmp_path / "v1.mp4").write_bytes(b"\x00" * 64)
    auth = AuthSettings.from_env(
        {"VPV_ADMIN_USER": "admin", "VPV_ADMIN_PASSWORD": "pw", "VPV_SESSION_SECRET": "k"})
    jobs = JobManager(tmp_path, run_fn=_pass_result)
    handler = type("H", (viewer._Handler,),
                   {"root": tmp_path.resolve(), "auth": auth, "jobs": jobs})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def _req(base, path, data=None, cookie=None, method=None):
    headers = {}
    if cookie:
        headers["Cookie"] = cookie
    if data is not None:
        data = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.headers.get("Set-Cookie"), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Set-Cookie"), e.read().decode()


def _login(base):
    status, cookie, _ = _req(base, "/api/login", {"username": "admin", "password": "pw"})
    assert status == 200 and cookie
    return cookie.split(";")[0]


def test_healthz_public(server):
    status, _, body = _req(server, "/healthz")
    assert status == 200
    assert json.loads(body)["ok"] is True


def test_me_unauthorized_then_ok(server):
    status, _, body = _req(server, "/api/me")
    assert status == 401
    assert json.loads(body)["authRequired"] is True
    cookie = _login(server)
    status, _, body = _req(server, "/api/me", cookie=cookie)
    assert status == 200
    assert json.loads(body)["user"] == "admin"


def test_protected_endpoints_require_auth(server):
    assert _req(server, "/api/videos")[0] == 401
    assert _req(server, "/media/v1.mp4")[0] == 401
    assert _req(server, "/api/runs")[0] == 401
    cookie = _login(server)
    assert _req(server, "/api/videos", cookie=cookie)[0] == 200
    assert _req(server, "/media/v1.mp4", cookie=cookie)[0] == 200


def test_bad_login_rejected(server):
    status, _, _ = _req(server, "/api/login", {"username": "admin", "password": "nope"})
    assert status == 401


def test_logout_clears_session(server):
    cookie = _login(server)
    assert _req(server, "/api/videos", cookie=cookie)[0] == 200
    status, set_cookie, _ = _req(server, "/api/logout", {}, cookie=cookie)
    assert status == 200
    assert "Max-Age=0" in set_cookie


def test_run_lifecycle(server):
    cookie = _login(server)
    status, _, body = _req(server, "/api/run", {"url": "https://ex.com/v"}, cookie=cookie)
    assert status == 202
    job_id = json.loads(body)["id"]

    deadline = time.time() + 3
    final = None
    while time.time() < deadline:
        status, _, body = _req(server, f"/api/runs/{job_id}", cookie=cookie)
        assert status == 200
        final = json.loads(body)
        if final["status"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert final["status"] == "passed"

    status, _, body = _req(server, "/api/runs", cookie=cookie)
    assert status == 200
    assert any(j["id"] == job_id for j in json.loads(body))


def test_run_requires_auth_and_validates(server):
    assert _req(server, "/api/run", {"url": "https://ex.com/v"})[0] == 401  # no cookie
    cookie = _login(server)
    assert _req(server, "/api/run", {}, cookie=cookie)[0] == 400  # missing url


def test_static_shell_is_public(server):
    # The SPA shell must load without auth (it renders its own login screen).
    status, _, _ = _req(server, "/")
    assert status == 200
