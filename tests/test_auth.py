"""Tests for session-cookie auth (src/vpv/auth.py)."""

from __future__ import annotations

import time

from vpv.auth import AuthSettings, parse_cookies


def _settings(**env):
    base = {"VPV_ADMIN_USER": "admin", "VPV_ADMIN_PASSWORD": "s3cret", "VPV_SESSION_SECRET": "k"}
    base.update(env)
    return AuthSettings.from_env(base)


def test_auth_disabled_when_no_password():
    a = AuthSettings.from_env({})
    assert a.enabled is False
    assert a.check("anyone", "anything") is True  # open when disabled


def test_check_credentials():
    a = _settings()
    assert a.enabled is True
    assert a.check("admin", "s3cret") is True
    assert a.check("admin", "wrong") is False
    assert a.check("root", "s3cret") is False


def test_session_roundtrip():
    a = _settings()
    token = a.make_session("admin")
    assert a.verify_session(token) == "admin"


def test_session_rejects_tampering():
    a = _settings()
    token = a.make_session("admin")
    assert a.verify_session(token + "x") is None
    body, _, _sig = token.partition(".")
    assert a.verify_session(body + ".deadbeef") is None
    assert a.verify_session(None) is None
    assert a.verify_session("garbage") is None


def test_session_rejects_expired():
    a = _settings()
    token = a.make_session("admin", ttl_s=-1)
    assert a.verify_session(token) is None


def test_session_wrong_secret():
    a = _settings(VPV_SESSION_SECRET="k1")
    b = _settings(VPV_SESSION_SECRET="k2")
    token = a.make_session("admin")
    assert b.verify_session(token) is None


def test_random_secret_survives_within_process_only():
    a = AuthSettings.from_env({"VPV_ADMIN_PASSWORD": "p"})  # no secret => random
    token = a.make_session("admin")
    assert a.verify_session(token) == "admin"  # same instance ok


def test_parse_cookies():
    assert parse_cookies("a=1; b=2; c=") == {"a": "1", "b": "2", "c": ""}
    assert parse_cookies(None) == {}
    assert parse_cookies("") == {}
