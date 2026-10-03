"""Authentication: the backend never trusts an identity sent by the browser.

The frontend signs in with Supabase and sends the resulting access token as
``Authorization: Bearer <token>``. This module asks Supabase who that token
belongs to (``GET /auth/v1/user``). Only the public anon key is needed for
that call, so no service-role key or JWT secret is stored anywhere.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from backend.config import Settings


class AuthError(Exception):
    """Raised when a request cannot be authenticated."""

    def __init__(self, message: str, status_code: int = 401):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class AuthUser:
    user_id: str
    email: str
    name: str | None = None
    is_dev: bool = False
    # Entered by the surveyor and kept in their Supabase profile
    # (user_metadata). Nothing checks it against a government register.
    govt_surveyor_id: str | None = None


UserFetcher = Callable[[str, str, str], dict]

_CACHE_TTL_SECONDS = 60.0
_cache: dict[str, tuple[float, AuthUser]] = {}
_cache_lock = threading.Lock()


def _fetch_supabase_user(supabase_url: str, anon_key: str, token: str) -> dict:
    request = urllib.request.Request(
        f"{supabase_url}/auth/v1/user",
        headers={
            "Authorization": f"Bearer {token}",
            "apikey": anon_key,
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise AuthError("Session is invalid or has expired. Sign in again.") from exc
        raise AuthError(
            f"The identity provider rejected the request (HTTP {exc.code}).", 503
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AuthError(
            "The identity provider could not be reached, so the session could not be verified.",
            503,
        ) from exc


def extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise AuthError("Sign in to use Cadastra Vision.")
    scheme, _, token = authorization.strip().partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("Authorization header must be 'Bearer <access token>'.")
    return token.strip()


def verify_token(
    token: str,
    settings: Settings,
    fetch: UserFetcher = _fetch_supabase_user,
    now: Callable[[], float] = time.monotonic,
) -> AuthUser:
    """Resolve an access token to a user, with a short positive cache."""

    if not settings.supabase_url or not settings.supabase_key:
        raise AuthError(
            "Authentication is not configured on the server: set SUPABASE_URL and SUPABASE_KEY.",
            503,
        )

    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _cache_lock:
        cached = _cache.get(digest)
        if cached and cached[0] > now():
            return cached[1]

    payload = fetch(settings.supabase_url, settings.supabase_key, token)
    user_id = payload.get("id")
    email = (payload.get("email") or "").strip().lower()
    if not user_id or not email:
        raise AuthError("The session does not identify a user. Sign in again.")

    metadata = payload.get("user_metadata") or {}
    name = metadata.get("full_name") or metadata.get("name") or None
    govt_id = str(metadata.get("govt_surveyor_id") or "").strip() or None

    user = AuthUser(user_id=str(user_id), email=email, name=name, govt_surveyor_id=govt_id)
    with _cache_lock:
        if len(_cache) > 2048:
            _cache.clear()
        _cache[digest] = (now() + _CACHE_TTL_SECONDS, user)
    return user


def authenticate(authorization: str | None, settings: Settings) -> AuthUser:
    """Entry point used by the API dependency."""

    if settings.auth_mode == "off":
        return AuthUser(
            user_id="local-dev",
            email=settings.dev_user_email.lower(),
            name="Local development session",
            is_dev=True,
        )
    return verify_token(extract_bearer(authorization), settings)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
