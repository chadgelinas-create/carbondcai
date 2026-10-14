"""Battery monitoring, connection settings, and live updates."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from sqlalchemy.exc import SQLAlchemyError

from skid2.application.devices import DeviceManager
from skid2.backend.action_audit import AuditedActionRoute
from skid2.backend.auth import COOKIE, current_user, require_admin
from skid2.backend.schemas import CurrentStateResponse, DeviceConfigRequest
from skid2.persistence.accounts import Accounts, User
from skid2.settings import get_settings

router = APIRouter(prefix="/api/v1", route_class=AuditedActionRoute)


def manager(request: Request) -> DeviceManager:
    runtime = getattr(request.app.state, "device_manager", None)
    if not isinstance(runtime, DeviceManager):
        raise HTTPException(503, "Battery monitoring is unavailable")
    return runtime


def snapshot(runtime: DeviceManager) -> dict[str, Any]:
    state = runtime.state.snapshot()
    connection = runtime.connection
    if connection == "connected" and state.freshness.value != "live":
        connection = "stale"
    return {
        "revision": runtime.revision,
        "mode": None if runtime.active is None else runtime.active["mode"],
        "connection": connection,
        "message": "The last reading is out of date. Waiting for new data."
        if connection == "stale"
        else runtime.message,
        "state": CurrentStateResponse.from_domain(state).model_dump(mode="json"),
        "stale_seconds": 3 if runtime.active is None else runtime.active["stale_seconds"],
    }


@router.get("/monitor")
def monitor(request: Request, user: Annotated[User, Depends(current_user)]) -> dict[str, Any]:
    return snapshot(manager(request))


@router.get("/config")
def configuration(
    request: Request, user: Annotated[User, Depends(require_admin)]
) -> dict[str, Any]:
    try:
        return manager(request).config.load()
    except SQLAlchemyError:
        raise HTTPException(503, "Saved settings are unavailable") from None


@router.post("/config")
async def save_configuration(
    body: DeviceConfigRequest, request: Request, user: Annotated[User, Depends(require_admin)]
) -> dict[str, Any]:
    raise HTTPException(
        409, "Save these settings for review in Settings history before applying them."
    )


@router.websocket("/monitor/stream")
async def stream(socket: WebSocket) -> None:
    settings = get_settings()
    accounts = getattr(socket.app.state, "accounts", None)
    runtime = getattr(socket.app.state, "device_manager", None)
    if (
        socket.headers.get("origin") not in settings.login_origins()
        or not isinstance(accounts, Accounts)
        or not isinstance(runtime, DeviceManager)
    ):
        await socket.close(code=1008)
        return
    token = socket.cookies.get(COOKIE, "")
    accepted = False
    try:
        while True:
            user = await asyncio.to_thread(accounts.current, token)
            if user is None:
                await socket.close(code=1008)
                return
            if not accepted:
                await socket.accept()
                accepted = True
            # Slow browsers must not hold the reader or grow an unbounded queue.
            async with asyncio.timeout(5):
                await socket.send_json(snapshot(runtime))
            await asyncio.sleep(1)
    except (WebSocketDisconnect, TimeoutError, OSError):
        return
    except SQLAlchemyError:
        await socket.close(code=1011)
