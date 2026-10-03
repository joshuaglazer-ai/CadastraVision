"""FastAPI dependencies shared by every router."""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException, Query

from backend.config import settings
from backend.core import runtime
from backend.core.auth import AuthError, AuthUser, authenticate
from backend.core.store import Store, get_store
from backend.services.assignment_service import SurveyorContext, resolve_context


def current_user(authorization: Optional[str] = Header(default=None)) -> AuthUser:
    """The authenticated account. Identity comes from the access token; a
    surveyor id sent by the browser is never trusted."""

    try:
        return authenticate(authorization, settings)
    except AuthError as exc:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        raise HTTPException(status_code=exc.status_code, detail=exc.message, headers=headers)


def current_context(user: AuthUser = Depends(current_user)) -> SurveyorContext:
    return resolve_context(user, settings)


def store_dep() -> Store:
    return get_store()


def source_param(
    source: Optional[str] = Query(
        default=None,
        description="Layer source: 'existing' or 'job:<JOB-ID>'. Defaults to the existing layers.",
    ),
) -> str:
    try:
        return runtime.valid_source(source)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown layer source '{source}'.")


def service_error(exc: Exception) -> HTTPException:
    """Translate a service-layer error into an HTTP error."""

    return HTTPException(status_code=getattr(exc, "status_code", 400), detail=str(exc))
