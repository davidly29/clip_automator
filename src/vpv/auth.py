"""Session-cookie authentication for the VPV web control panel.

Stdlib only. A single admin credential is read from the environment; auth is
enforced only when a password is configured, so local ``vpv-view`` usage (and
the test suite) keep working unauthenticated while a deployment stays locked
down. The single-credential check is deliberately isolated in
:meth:`AuthSettings.check` so a multi-user store can replace it later without
touching the cookie machinery.

Sessions are stateless signed cookies: ``base64url(json_payload).hmac_sha256``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass

COOKIE_NAME = "vpv_session"
SESSION_TTL_S = 7 * 24 * 3600  # 7 days


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


@dataclass(frozen=True)
class AuthSettings:
    """Admin credentials + signing secret, with auth on/off derived from them."""

    user: str
    password: str        # empty string => auth disabled
    secret: bytes

    @classmethod
    def from_env(cls, env: dict | None = None) -> "AuthSettings":
        env = os.environ if env is None else env
        user = (env.get("VPV_ADMIN_USER") or "admin").strip()
        password = env.get("VPV_ADMIN_PASSWORD") or ""
        secret_str = env.get("VPV_SESSION_SECRET") or ""
        # A per-process random secret is fine when none is provided: it just
        # means sessions don't survive a restart. In a deployment, set
        # VPV_SESSION_SECRET so cookies remain valid across restarts/instances.
        secret = secret_str.encode("utf-8") if secret_str else secrets.token_bytes(32)
        return cls(user=user, password=password, secret=secret)

    @property
    def enabled(self) -> bool:
        return bool(self.password)

    def check(self, user: str, password: str) -> bool:
        """True if the submitted credentials match the single admin account."""
        if not self.enabled:
            return True
        u_ok = hmac.compare_digest(user or "", self.user)
        p_ok = hmac.compare_digest(password or "", self.password)
        return u_ok and p_ok

    # --- stateless signed session cookies ---
    def _sign(self, body: str) -> str:
        mac = hmac.new(self.secret, body.encode("ascii"), hashlib.sha256).hexdigest()
        return mac

    def make_session(self, user: str, ttl_s: int = SESSION_TTL_S) -> str:
        payload = {"u": user, "exp": int(time.time()) + ttl_s}
        body = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        return f"{body}.{self._sign(body)}"

    def verify_session(self, cookie: str | None) -> str | None:
        """Return the username from a valid, unexpired cookie, else None."""
        if not cookie or "." not in cookie:
            return None
        body, _, sig = cookie.partition(".")
        expected = self._sign(body)
        if not hmac.compare_digest(sig, expected):
            return None
        try:
            payload = json.loads(_b64d(body))
        except (ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or "exp" not in payload:
            return None
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        user = payload.get("u")
        return user if isinstance(user, str) else None


def parse_cookies(header: str | None) -> dict[str, str]:
    """Minimal Cookie header parser (name=value; name2=value2)."""
    out: dict[str, str] = {}
    if not header:
        return out
    for part in header.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name:
            out[name] = value
    return out
