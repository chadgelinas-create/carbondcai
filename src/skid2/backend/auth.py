"""Cookie sessions and server-side access rules."""

from __future__ import annotations

import asyncio
import secrets
import time
from collections import deque
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from skid2.persistence.accounts import Accounts, User
from skid2.settings import get_settings

router = APIRouter(prefix="/api/v1")
COOKIE = "skid2_session"
_login_attempts: deque[float] = deque()
_login_slots = asyncio.Semaphore(2)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


def accounts(request: Request) -> Accounts:
    store = getattr(request.app.state, "accounts", None)
    if not isinstance(store, Accounts):
        raise HTTPException(503, "Sign-in is unavailable")
    return store


def check_origin(request: Request) -> None:
    if request.headers.get("origin") not in get_settings().login_origins():
        raise HTTPException(403, "Request origin is not allowed")


def current_user(request: Request) -> User:
    try:
        user = accounts(request).current(request.cookies.get(COOKIE, ""))
    except SQLAlchemyError:
        raise HTTPException(503, "Sign-in is unavailable") from None
    if user is None:
        raise HTTPException(401, "Please sign in")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_origin(request)
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), user["csrf_token"]):
            raise HTTPException(403, "Request verification failed. Reload and try again.")
    return user


def require_admin(user: Annotated[User, Depends(current_user)]) -> User:
    if user["role"] != "admin":
        raise HTTPException(403, "Administrator access required")
    return user


def simulation_access(request: Request) -> None:
    if not get_settings().auth_enabled:
        return
    user = current_user(request)
    if request.method != "GET" and user["role"] not in {"operator", "admin"}:
        raise HTTPException(403, "Operator access required")


def protected_access(request: Request) -> None:
    if get_settings().auth_enabled:
        current_user(request)


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response) -> User:
    check_origin(request)
    now = time.monotonic()
    while _login_attempts and _login_attempts[0] <= now - 60:
        _login_attempts.popleft()
    if len(_login_attempts) >= 30:
        raise HTTPException(429, "Too many sign-in attempts. Please wait a minute.")
    _login_attempts.append(now)
    try:
        async with _login_slots:
            result = await asyncio.to_thread(accounts(request).login, body.username, body.password)
    except SQLAlchemyError:
        raise HTTPException(503, "Sign-in is unavailable") from None
    if result is None:
        raise HTTPException(
            401, "Username or password is incorrect, or sign-in is temporarily locked"
        )
    token, user = result
    settings = get_settings()
    response.set_cookie(
        COOKIE,
        token,
        max_age=settings.session_seconds,
        secure=settings.secure_cookies,
        httponly=True,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        "skid2_csrf",
        user["csrf_token"],
        secure=get_settings().secure_cookies,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return user


@router.get("/session")
def session(response: Response, user: Annotated[User, Depends(current_user)]) -> User:
    response.set_cookie(
        "skid2_csrf",
        user["csrf_token"],
        secure=get_settings().secure_cookies,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return user


@router.post("/logout", status_code=204)
def logout(
    request: Request, response: Response, user: Annotated[User, Depends(current_user)]
) -> None:
    accounts(request).logout(request.cookies.get(COOKIE, ""))
    response.delete_cookie(COOKIE, path="/")
    response.delete_cookie("skid2_csrf", path="/")
