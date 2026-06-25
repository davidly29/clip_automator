"""Tests for the vpv-view vertical video viewer."""

from __future__ import annotations

import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from vpv import viewer


def _make_video(path, data=b"\x00\x11\x22\x33" * 64):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_find_videos_and_api(tmp_path):
    _make_video(tmp_path / "20260614-1_dom_id" / "snippet.mp4")
    _make_video(tmp_path / "clip2.webm")
    (tmp_path / "20260614-1_dom_id" / "frame_000.png").write_bytes(b"x")  # used as poster
    (tmp_path / "notes.txt").write_text("nope")

    vids = viewer.find_videos(tmp_path)
    assert len(vids) == 2
    assert all(v.suffix.lower() in viewer.VIDEO_EXTS for v in vids)

    items = viewer.api_videos(tmp_path)
    by_src = {it["src"]: it for it in items}
    snip = by_src["/media/20260614-1_dom_id/snippet.mp4"]
    assert snip["label"] == "20260614-1_dom_id"   # snippet => folder name
    assert snip["poster"] == "/media/20260614-1_dom_id/frame_000.png"
    assert by_src["/media/clip2.webm"]["label"] == "clip2"


def test_compose_filter_single_no_audio():
    filt, amap = viewer._compose_filter(1, [False])
    assert filt == "[0:v]scale=-2:720,setsar=1[v]"
    assert amap is None


def test_compose_filter_two_with_audio():
    filt, amap = viewer._compose_filter(2, [True, True])
    assert "[v0][v1]hstack=inputs=2[v]" in filt
    assert "amix=inputs=2" in filt
    assert amap == "[a]"


def test_compose_filter_three_one_audio():
    filt, amap = viewer._compose_filter(3, [False, True, False])
    assert "hstack=inputs=3[v]" in filt
    assert "amix" not in filt          # only one audio stream -> mapped directly
    assert amap == "1:a"


def test_compose_filter_two_no_audio():
    filt, amap = viewer._compose_filter(2, [False, False])
    assert "hstack=inputs=2[v]" in filt
    assert amap is None


def test_safe_upload_name():
    assert viewer._safe_upload_name("../../etc/passwd") is None     # no video ext
    assert viewer._safe_upload_name("notes.txt") is None
    assert viewer._safe_upload_name("a/b/c.mp4") == "c.mp4"         # path stripped
    assert viewer._safe_upload_name("weird name!.webm") == "weird_name_.webm"
    assert viewer._safe_upload_name("") is None


@pytest.fixture
def server(tmp_path):
    _make_video(tmp_path / "v1.mp4")
    handler = type("H", (viewer._Handler,), {"root": tmp_path.resolve()})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def test_index_served(server):
    # Serves the built React SPA when present, else the fallback page — either
    # way it's HTML with a 200.
    with urllib.request.urlopen(server + "/") as r:
        assert r.status == 200
        assert "text/html" in r.headers["Content-Type"]
        body = r.read().decode()
    assert ('id="root"' in body) or ("data-src=" in body)


def test_api_videos(server):
    import json
    with urllib.request.urlopen(server + "/api/videos") as r:
        assert r.status == 200
        assert "application/json" in r.headers["Content-Type"]
        data = json.loads(r.read().decode())
    assert isinstance(data, list) and len(data) == 1
    assert data[0]["src"] == "/media/v1.mp4"
    assert data[0]["label"] == "v1"
    assert "passed" in data[0] and "poster" in data[0]


def test_media_served_with_range(server):
    # Full request
    with urllib.request.urlopen(server + "/media/v1.mp4") as r:
        assert r.status == 200
        assert r.headers["Accept-Ranges"] == "bytes"
        full = r.read()
    assert len(full) == 256

    # Range request -> 206 partial
    req = urllib.request.Request(server + "/media/v1.mp4", headers={"Range": "bytes=0-9"})
    with urllib.request.urlopen(req) as r:
        assert r.status == 206
        assert r.headers["Content-Range"] == "bytes 0-9/256"
        part = r.read()
    assert len(part) == 10


def test_upload_saves_and_serves(server, tmp_path):
    import json
    payload = b"\x00\x11\x22\x33" * 32
    req = urllib.request.Request(
        server + "/api/upload?name=desktop%20clip.mp4",
        data=payload, method="POST", headers={"Content-Type": "video/mp4"},
    )
    with urllib.request.urlopen(req) as r:
        assert r.status == 200
        out = json.loads(r.read().decode())
    assert out["src"].startswith("/media/uploads/")
    assert out["src"].endswith(".mp4")

    saved = list((tmp_path / "uploads").glob("*.mp4"))
    assert len(saved) == 1
    assert saved[0].read_bytes() == payload

    # and it's immediately servable
    with urllib.request.urlopen(server + out["src"]) as r:
        assert r.status == 200
        assert r.read() == payload


def test_upload_rejects_non_video(server):
    import urllib.error
    req = urllib.request.Request(
        server + "/api/upload?name=notes.txt",
        data=b"hello", method="POST", headers={"Content-Type": "text/plain"},
    )
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req)
    assert ei.value.code == 400


def test_media_path_traversal_blocked(server):
    import urllib.error
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(server + "/media/..%2f..%2fetc%2fpasswd")
    assert ei.value.code == 404
