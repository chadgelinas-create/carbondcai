"""EMS REST + WebSocket API boundary."""

from __future__ import annotations

import csv
import io
import time
from collections import deque
from contextlib import suppress
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from skid2.application.simulation import SimulationService
from skid2.backend.auth import accounts as lab_accounts
from skid2.backend.auth import check_origin
from skid2.backend.auth import current_user as lab_session
from skid2.ems.auth import AuthError, AuthService, AuthUser
from skid2.ems.mailer import invite_url, send_authorization_email
from skid2.settings import get_settings
from skid2.ems.charger import ChargerAvailability
from skid2.ems.charger_handshake import HandshakeMachine
from skid2.ems.charger_sessions import ChargerSessionStore
from skid2.ems.charger_yard import ChargerYard
from skid2.ems.config import ConfigError, ConfigService
from skid2.ems.device_manager import DeviceManager
from skid2.ems.groups import DEVICE_GROUPS
from skid2.ems.hub import EmsHub
from skid2.ems.mppt import MpptController
from skid2.ems.setpoints import apply_sim_setpoints, set_stabiliti_comms
from skid2.ems.supervisor import Supervisor
from skid2.persistence.accounts import User as LabUser
from skid2.persistence.telemetry import TelemetryRepository

router = APIRouter(prefix="/api/v1")
_forgot_attempts: deque[float] = deque()
_FORGOT_MESSAGE = "If that email is authorized, we sent a login link. Check your email."


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class LoginResponse(BaseModel):
    token: str
    username: str
    role: str
    email: str = ""
    display_name: str = ""
    company: str = ""
    system_name: str = ""
    active: bool = True
    invite_sent: bool = False
    invite_url: str = ""
    mail_status: str = ""


class ForgotPasswordRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class ForgotPasswordResponse(BaseModel):
    mail_status: str


class InvitePreviewResponse(BaseModel):
    username: str
    email: str
    display_name: str
    purpose: str


class AcceptInviteRequest(BaseModel):
    token: str = Field(min_length=8, max_length=128)
    password: str = Field(min_length=8, max_length=128)


class ProfileUpdateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    email: str = Field(min_length=3, max_length=254)
    display_name: str = Field(min_length=1, max_length=120)


class PasswordResetRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class AdminUserUpdateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    email: str = Field(min_length=3, max_length=254)
    display_name: str = Field(min_length=1, max_length=120)
    company: str = Field(min_length=1, max_length=120)
    system_name: str = Field(min_length=1, max_length=120)
    role: Literal["viewer", "operator", "manager", "admin"]


class AdminPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


class AdminUserCreateRequest(BaseModel):
    username: str = Field(default="", max_length=64)
    password: str = Field(default="", max_length=128)
    email: str = Field(min_length=3, max_length=254)
    display_name: str = Field(default="", max_length=120)
    company: str = Field(default="Carbon DC", max_length=120)
    system_name: str = Field(default="Skid 2", max_length=120)
    role: Literal["viewer", "operator", "manager", "admin"] = "operator"


class AdminActiveRequest(BaseModel):
    active: bool


class AccountActivityResponse(BaseModel):
    occurred_at: str
    actor: str
    action: str
    subject: str
    gate: str
    detail: str = ""


class ModeResponse(BaseModel):
    data_source: Literal["sim", "live"]


class ModeRequest(BaseModel):
    data_source: Literal["sim", "live"]


class ConfigItemResponse(BaseModel):
    group_id: str
    key: str
    value: float | None
    value_text: str | None
    unit: str
    updated_at: str
    updated_by: str
    outcome: str = "applied"
    requested: str | None = None
    effective: str | None = None
    message: str = ""


class ConfigWriteRequest(BaseModel):
    key: str
    value: float | str


class TelemetryPointResponse(BaseModel):
    point_id: str
    device_id: str
    group_id: str
    label: str
    value: float
    unit: str
    quality: str
    source_timestamp: str
    stale: bool


class PortResponse(BaseModel):
    port_id: str
    label: str
    occupied: bool
    requested_kw: float
    allocated_kw: float
    energy_kwh: float = 0.0
    session_id: int | None = None
    capped: bool = False


class SessionResponse(BaseModel):
    id: int
    port_id: str
    status: str
    energy_kwh: float
    power_kw: float
    capped: bool
    started_at: str
    ended_at: str | None = None


class PortRequest(BaseModel):
    occupied: bool
    requested_kw: float = 0.0


class ChargerStatusResponse(BaseModel):
    mode: str
    allow_new_sessions: bool
    energy_cap_kwh: float | None
    terminate_active: bool
    driver_message: str
    soc: float
    demand_kw: float = 0.0
    available_kw: float = 0.0
    unmet_kw: float = 0.0
    ports: list[PortResponse] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    handshake: str = "idle"
    sessions: list[SessionResponse] = Field(default_factory=list)
    pms_status: int = 0
    pms_status_label: str = "normal"
    time_remaining_min: float = 999.0
    pv_kw: float = 0.0


class SupervisorResponse(BaseModel):
    bus_voltage: float
    system_status: int
    available_power_kw: float
    heartbeats_ok: int
    heartbeats_total: int
    status_label: str
    heartbeats: dict[str, bool]


def _auth(request: Request) -> AuthService:
    service = getattr(request.app.state, "ems_auth", None)
    if not isinstance(service, AuthService):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "auth not ready")
    return service


def _hub(request: Request) -> EmsHub:
    hub = getattr(request.app.state, "ems_hub", None)
    if not isinstance(hub, EmsHub):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "ems hub not ready")
    return hub


def _config(request: Request) -> ConfigService:
    service = getattr(request.app.state, "ems_config", None)
    if not isinstance(service, ConfigService):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "config not ready")
    return service


def _manager(request: Request) -> DeviceManager:
    manager = getattr(request.app.state, "ems_device_manager", None)
    if not isinstance(manager, DeviceManager):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "device manager not ready")
    return manager


def _yard(request: Request) -> ChargerYard:
    yard = getattr(request.app.state, "ems_charger_yard", None)
    if not isinstance(yard, ChargerYard):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "charger yard not ready")
    return yard


def _supervisor(request: Request) -> Supervisor:
    service = getattr(request.app.state, "ems_supervisor", None)
    if not isinstance(service, Supervisor):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "supervisor not ready")
    return service


def _charger(request: Request) -> ChargerAvailability:
    service = getattr(request.app.state, "ems_charger", None)
    if not isinstance(service, ChargerAvailability):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "charger logic not ready")
    return service


def _sessions(request: Request) -> ChargerSessionStore:
    store = getattr(request.app.state, "ems_charger_sessions", None)
    if not isinstance(store, ChargerSessionStore):
        return ChargerSessionStore()
    return store


def _handshake(request: Request) -> HandshakeMachine:
    machine = getattr(request.app.state, "ems_handshake", None)
    if not isinstance(machine, HandshakeMachine):
        return HandshakeMachine()
    return machine


def _mppt(request: Request) -> MpptController:
    controller = getattr(request.app.state, "ems_mppt", None)
    if not isinstance(controller, MpptController):
        return MpptController()
    return controller


def _account(user: AuthUser, token: str) -> LoginResponse:
    return LoginResponse(
        token=token,
        username=user.username,
        role=user.role,
        email=user.email,
        display_name=user.display_name,
        company=user.company,
        system_name=user.system_name,
        active=user.active,
    )


def _stored_account(account: AuthUser) -> LoginResponse:
    return LoginResponse(
        token="",
        username=account.username,
        role=account.role,
        email=account.email,
        display_name=account.display_name,
        company=account.company,
        system_name=account.system_name,
        active=account.active,
    )


def _deliver_invite(
    request: Request,
    actor: AuthUser,
    username: str,
    *,
    purpose: str = "invite",
) -> LoginResponse:
    stored = _auth(request).load_user(username)
    token = _auth(request).issue_invite(actor, username, purpose=purpose)
    settings = get_settings()
    mail = send_authorization_email(
        settings,
        to_address=stored.email,
        display_name=stored.display_name,
        token=token,
        purpose=purpose,
    )
    visible_link = settings.environment == "development" or not mail.sent
    return _stored_account(stored).model_copy(
        update={
            "invite_sent": mail.sent,
            "mail_status": mail.detail,
            "invite_url": invite_url(settings, token) if visible_link else "",
        }
    )


def current_user(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> AuthUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bearer token required")
    token = authorization.split(" ", 1)[1].strip()
    try:
        return _auth(request).parse_token(token)
    except AuthError as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error)) from error


def _limit_forgot_password() -> None:
    now = time.monotonic()
    while _forgot_attempts and _forgot_attempts[0] <= now - 60:
        _forgot_attempts.popleft()
    if len(_forgot_attempts) >= 10:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Please wait a minute.")
    _forgot_attempts.append(now)


@router.post("/auth/forgot-password", response_model=ForgotPasswordResponse)
def forgot_password(body: ForgotPasswordRequest, request: Request) -> ForgotPasswordResponse:
    check_origin(request)
    _limit_forgot_password()
    try:
        issued = _auth(request).request_password_reset(body.email)
    except AuthError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
    if issued:
        token, to_address, display_name = issued
        send_authorization_email(
            get_settings(),
            to_address=to_address,
            display_name=display_name,
            token=token,
            purpose="password_change",
        )
    return ForgotPasswordResponse(mail_status=_FORGOT_MESSAGE)


@router.post("/auth/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request) -> LoginResponse:
    try:
        user, token = _auth(request).authenticate(body.username, body.password)
    except AuthError as error:
        detail = str(error)
        if "email" in detail.lower():
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail) from error
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Username or password is incorrect"
        ) from error
    return _account(user, token)


@router.get("/auth/from-lab", response_model=LoginResponse)
def from_lab(
    request: Request,
    lab: Annotated[LabUser, Depends(lab_session)],
) -> LoginResponse:
    if lab["role"] != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator access required")
    auth = _auth(request)
    try:
        stored = auth.load_user(lab["username"])
    except AuthError:
        stored = AuthUser(username=lab["username"], role="admin")
    else:
        stored = AuthUser(
            username=stored.username,
            role="admin",
            email=stored.email,
            display_name=stored.display_name,
            company=stored.company,
            system_name=stored.system_name,
            active=stored.active,
        )
    return _account(stored, auth.issue_token(stored))


@router.get("/auth/me", response_model=LoginResponse)
def me(user: Annotated[AuthUser, Depends(current_user)], request: Request) -> LoginResponse:
    try:
        stored = _auth(request).load_user(user.username)
    except AuthError as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in again") from error
    return _account(stored, _auth(request).issue_token(stored))


@router.patch("/auth/me", response_model=LoginResponse)
def update_me(
    body: ProfileUpdateRequest,
    request: Request,
    user: Annotated[AuthUser, Depends(current_user)],
) -> LoginResponse:
    try:
        stored, token = _auth(request).update_own_profile(
            user,
            username=body.username,
            email=body.email,
            display_name=body.display_name,
        )
    except AuthError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    return _account(stored, token)


@router.post("/auth/password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    body: PasswordResetRequest,
    request: Request,
    user: Annotated[AuthUser, Depends(current_user)],
) -> None:
    try:
        _auth(request).reset_own_password(user, body.current_password, body.new_password)
    except AuthError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error


@router.get("/auth/users", response_model=list[LoginResponse])
def list_users(
    request: Request,
    user: Annotated[AuthUser, Depends(current_user)],
) -> list[LoginResponse]:
    try:
        accounts = _auth(request).list_users(user)
    except AuthError as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(error)) from error
    return [_stored_account(account) for account in accounts]


@router.get("/auth/activity", response_model=list[AccountActivityResponse])
def account_activity(
    request: Request,
    user: Annotated[AuthUser, Depends(current_user)],
) -> list[AccountActivityResponse]:
    try:
        rows = _auth(request).list_activity(user)
    except AuthError as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(error)) from error
    return [
        AccountActivityResponse(
            occurred_at=row.occurred_at.isoformat(),
            actor=row.actor,
            action=row.action,
            subject=row.subject,
            gate=row.gate,
            detail=row.detail,
        )
        for row in rows
    ]


@router.post("/auth/users", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    body: AdminUserCreateRequest,
    request: Request,
    user: Annotated[AuthUser, Depends(current_user)],
) -> LoginResponse:
    try:
        stored = _auth(request).create_user(
            user,
            username=body.username or body.email,
            password=body.password,
            email=body.email,
            display_name=body.display_name,
            company=body.company,
            system_name=body.system_name,
            role=body.role,
        )
        invited = _deliver_invite(request, user, stored.username)
    except AuthError as error:
        status_code = (
            status.HTTP_403_FORBIDDEN
            if str(error) == "insufficient role"
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code, str(error)) from error
    if stored.role == "admin" and body.password.strip():
        with suppress(ValueError, HTTPException, SQLAlchemyError):
            lab_accounts(request).grant_access(stored.username, body.password, "admin")
    return invited


@router.post("/auth/users/{target_username}/invite", response_model=LoginResponse)
def resend_invite(
    target_username: str,
    request: Request,
    user: Annotated[AuthUser, Depends(current_user)],
) -> LoginResponse:
    try:
        return _deliver_invite(
            request,
            user,
            target_username,
            purpose="password_change",
        )
    except AuthError as error:
        status_code = (
            status.HTTP_403_FORBIDDEN
            if str(error) == "insufficient role"
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code, str(error)) from error


@router.get("/auth/invite", response_model=InvitePreviewResponse)
def preview_invite(token: str, request: Request) -> InvitePreviewResponse:
    try:
        preview = _auth(request).read_invite(token)
    except AuthError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    return InvitePreviewResponse(
        username=preview.username,
        email=preview.email,
        display_name=preview.display_name,
        purpose=preview.purpose,
    )


@router.post("/auth/invite", response_model=LoginResponse)
def accept_invite(body: AcceptInviteRequest, request: Request) -> LoginResponse:
    check_origin(request)
    try:
        stored = _auth(request).accept_invite(body.token, body.password)
        user, token = _auth(request).authenticate(stored.username, body.password)
    except AuthError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    if stored.role == "admin":
        with suppress(ValueError, HTTPException, SQLAlchemyError):
            lab_accounts(request).grant_access(stored.username, body.password, "admin")
    return _account(user, token)


@router.post("/auth/users/{target_username}/active", response_model=LoginResponse)
def set_user_active(
    target_username: str,
    body: AdminActiveRequest,
    request: Request,
    user: Annotated[AuthUser, Depends(current_user)],
) -> LoginResponse:
    try:
        stored = _auth(request).set_user_active(user, target_username, body.active)
    except AuthError as error:
        status_code = (
            status.HTTP_403_FORBIDDEN
            if str(error) in {"insufficient role", "You cannot disable your own account"}
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code, str(error)) from error
    return _stored_account(stored)


@router.patch("/auth/users/{target_username}", response_model=LoginResponse)
def update_user(
    target_username: str,
    body: AdminUserUpdateRequest,
    request: Request,
    user: Annotated[AuthUser, Depends(current_user)],
) -> LoginResponse:
    try:
        stored = _auth(request).update_user(
            user,
            target_username,
            username=body.username,
            email=body.email,
            display_name=body.display_name,
            company=body.company,
            system_name=body.system_name,
            role=body.role,
        )
    except AuthError as error:
        status_code = (
            status.HTTP_403_FORBIDDEN
            if str(error) == "insufficient role"
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code, str(error)) from error
    return _stored_account(stored)


@router.post("/auth/users/{target_username}/password", status_code=status.HTTP_204_NO_CONTENT)
def reset_user_password(
    target_username: str,
    body: AdminPasswordRequest,
    request: Request,
    user: Annotated[AuthUser, Depends(current_user)],
) -> None:
    try:
        _auth(request).reset_user_password(user, target_username, body.new_password)
    except AuthError as error:
        status_code = (
            status.HTTP_403_FORBIDDEN
            if str(error) == "insufficient role"
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code, str(error)) from error


@router.get("/ems/mode", response_model=ModeResponse)
def get_mode(request: Request, _user: Annotated[AuthUser, Depends(current_user)]) -> ModeResponse:
    return ModeResponse(data_source=_manager(request).data_source)  # type: ignore[arg-type]


@router.put("/ems/mode", response_model=ModeResponse)
def set_mode(
    body: ModeRequest,
    request: Request,
    user: Annotated[AuthUser, Depends(current_user)],
) -> ModeResponse:
    try:
        _auth(request).require_role(user, "operator", "manager", "admin")
    except AuthError as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(error)) from error
    _manager(request).set_data_source(body.data_source)
    return ModeResponse(data_source=body.data_source)


@router.get("/ems/telemetry/{group_id}", response_model=list[TelemetryPointResponse])
def telemetry_group(
    group_id: str,
    request: Request,
    _user: Annotated[AuthUser, Depends(current_user)],
) -> list[TelemetryPointResponse]:
    if group_id not in DEVICE_GROUPS and group_id != "all":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown group")
    points = _hub(request).snapshot(None if group_id == "all" else group_id)
    return [
        TelemetryPointResponse(
            point_id=point.point_id,
            device_id=point.device_id,
            group_id=point.group_id,
            label=point.label,
            value=0.0 if point.stale else point.value,
            unit=point.unit,
            quality=point.quality,
            source_timestamp=point.source_timestamp.isoformat(),
            stale=point.stale,
        )
        for point in points
    ]


@router.get("/ems/config/{group_id}", response_model=list[ConfigItemResponse])
def get_config(
    group_id: str,
    request: Request,
    _user: Annotated[AuthUser, Depends(current_user)],
) -> list[ConfigItemResponse]:
    try:
        items = _config(request).list_group(group_id)
    except (ConfigError, KeyError) as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    return [
        ConfigItemResponse(
            group_id=item.group_id,
            key=item.key,
            value=item.value,
            value_text=item.value_text,
            unit=item.unit,
            updated_at=item.updated_at.isoformat(),
            updated_by=item.updated_by,
        )
        for item in items
    ]


@router.post("/ems/config/{group_id}", response_model=ConfigItemResponse)
def write_config(
    group_id: str,
    body: ConfigWriteRequest,
    request: Request,
    user: Annotated[AuthUser, Depends(current_user)],
) -> ConfigItemResponse:
    try:
        _auth(request).require_role(user, "operator", "manager", "admin")
        item = _config(request).set_value(group_id, body.key, body.value, user.username)
    except AuthError as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(error)) from error
    except (ConfigError, KeyError) as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    applied: list[str] = []
    simulation = getattr(request.app.state, "simulation", None)
    if isinstance(simulation, SimulationService) and group_id in {
        "cnvtr",
        "solar",
        "charger",
        "stabiliti",
    }:
        try:
            applied = apply_sim_setpoints(
                simulation,
                _config(request),
                _hub(request),
                _mppt(request),
            )
        except (RuntimeError, ValueError):
            applied = []
    supervisor = getattr(request.app.state, "ems_supervisor", None)
    if isinstance(supervisor, Supervisor):
        supervisor.record_event(
            "config",
            f"{group_id}.{body.key} -> {body.value}",
            device_id=group_id,
        )
    effective = item.value_text if item.value_text is not None else str(item.value)
    outcome = "applied" if applied else "accepted"
    message = "; ".join(applied) if applied else f"{group_id}.{body.key} locked"
    return ConfigItemResponse(
        group_id=item.group_id,
        key=item.key,
        value=item.value,
        value_text=item.value_text,
        unit=item.unit,
        updated_at=item.updated_at.isoformat(),
        updated_by=item.updated_by,
        outcome=outcome,
        requested=str(body.value),
        effective=effective,
        message=message,
    )


@router.get("/ems/config/{group_id}/audit")
def config_audit(
    group_id: str,
    request: Request,
    _user: Annotated[AuthUser, Depends(current_user)],
) -> list[dict[str, Any]]:
    rows = _config(request).audit(group_id)
    return [
        {
            "group_id": row.group_id,
            "key": row.key,
            "old_value": row.old_value,
            "new_value": row.new_value,
            "changed_by": row.changed_by,
            "changed_at": row.changed_at.isoformat(),
        }
        for row in rows
    ]


@router.get("/ems/charger/status", response_model=ChargerStatusResponse)
def charger_status(
    request: Request,
    _user: Annotated[AuthUser, Depends(current_user)],
) -> ChargerStatusResponse:
    hub = _hub(request)
    soc_point = hub.get("bess.cluster.soc")
    soc = 0.0 if soc_point is None or soc_point.stale else soc_point.value
    decision = _charger(request).evaluate(soc)
    snap = _supervisor(request).snapshot()
    available = snap.available_power_kw
    yard = _yard(request).snapshot(available, allow_new=decision.allow_new_sessions)
    store = _sessions(request)
    handshake = _handshake(request)
    session_events = list(store.events())
    return ChargerStatusResponse(
        mode=decision.mode.value,
        allow_new_sessions=decision.allow_new_sessions,
        energy_cap_kwh=decision.energy_cap_kwh,
        terminate_active=decision.terminate_active,
        driver_message=decision.driver_message,
        soc=decision.soc,
        demand_kw=yard.demand_kw,
        available_kw=yard.available_kw,
        unmet_kw=yard.unmet_kw,
        ports=[
            PortResponse(
                port_id=port.port_id,
                label=port.label,
                occupied=port.occupied,
                requested_kw=port.requested_kw,
                allocated_kw=port.allocated_kw,
                energy_kwh=port.energy_kwh,
                session_id=port.session_id,
                capped=port.capped,
            )
            for port in yard.ports
        ],
        events=list(yard.events) + session_events[-10:],
        handshake=handshake.phase.value,
        pms_status=snap.charger_status,
        pms_status_label=snap.charger_status_label,
        time_remaining_min=snap.charger_time_remaining_min,
        pv_kw=snap.charger_pv_kw,
        sessions=[
            SessionResponse(
                id=row.id,
                port_id=row.port_id,
                status=row.status,
                energy_kwh=row.energy_kwh,
                power_kw=row.power_kw,
                capped=row.capped,
                started_at=row.started_at.isoformat(),
                ended_at=None if row.ended_at is None else row.ended_at.isoformat(),
            )
            for row in store.recent()
        ],
    )


@router.post("/ems/charger/ports/{port_id}", response_model=ChargerStatusResponse)
def set_charger_port(
    port_id: str,
    body: PortRequest,
    request: Request,
    user: Annotated[AuthUser, Depends(current_user)],
) -> ChargerStatusResponse:
    try:
        _auth(request).require_role(user, "operator", "manager", "admin")
    except AuthError as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(error)) from error
    hub = _hub(request)
    soc_point = hub.get("bess.cluster.soc")
    soc = 0.0 if soc_point is None or soc_point.stale else soc_point.value
    decision = _charger(request).evaluate(soc)
    yard = _yard(request)
    store = _sessions(request)
    before = next(
        (port for port in yard.snapshot(0.0, allow_new=True).ports if port.port_id == port_id),
        None,
    )
    try:
        yard.set_port(
            port_id,
            occupied=body.occupied,
            requested_kw=body.requested_kw,
            allow_new=decision.allow_new_sessions,
        )
    except KeyError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    occupied_before = False if before is None else before.occupied
    if body.occupied and not occupied_before:
        record = store.open_session(port_id, body.requested_kw)
        yard.bind_session(port_id, record.id)
    elif occupied_before and not body.occupied:
        store.close_session(
            port_id,
            0.0 if before is None else before.energy_kwh,
            capped=False if before is None else before.capped,
        )
    supervisor = _supervisor(request)
    supervisor.record_event(
        "charger",
        f"port {port_id} occupied={body.occupied} request={body.requested_kw} kW",
        device_id="dc600",
    )
    return charger_status(request, user)


@router.get("/ems/supervisor", response_model=SupervisorResponse)
def supervisor_status(
    request: Request,
    _user: Annotated[AuthUser, Depends(current_user)],
) -> SupervisorResponse:
    snap = _supervisor(request).snapshot()
    _supervisor(request).evaluate_alarms()
    return SupervisorResponse(
        bus_voltage=snap.bus_voltage,
        system_status=snap.system_status,
        available_power_kw=snap.available_power_kw,
        heartbeats_ok=snap.heartbeats_ok,
        heartbeats_total=snap.heartbeats_total,
        status_label=snap.status_label,
        heartbeats=dict(_hub(request).heartbeats),
    )


@router.get("/ems/historian/{point_id}/csv")
def historian_csv(
    point_id: str,
    request: Request,
    _user: Annotated[AuthUser, Depends(current_user)],
    limit: int = 500,
) -> dict[str, str]:
    repository = getattr(request.app.state, "ems_telemetry", None)
    if not isinstance(repository, TelemetryRepository):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "historian not ready")
    samples = repository.history(point_id, limit=min(max(limit, 1), 1000))
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["point_id", "source", "value", "unit", "quality", "source_timestamp", "receive_timestamp"]
    )
    for sample in samples:
        writer.writerow(
            [
                sample.point_id,
                sample.source,
                sample.value,
                sample.unit,
                sample.quality.value,
                sample.source_timestamp.isoformat(),
                sample.receive_timestamp.isoformat(),
            ]
        )
    return {
        "filename": f"{point_id}-{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')}.csv",
        "csv": buffer.getvalue(),
    }


@router.post("/ems/demo/comms-loss/{device_id}")
def inject_comms_loss(
    device_id: str,
    request: Request,
    user: Annotated[AuthUser, Depends(current_user)],
    clear: bool = False,
) -> dict[str, str]:
    try:
        _auth(request).require_role(user, "operator", "manager", "admin")
    except AuthError as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(error)) from error
    manager = _manager(request)
    simulation = getattr(request.app.state, "simulation", None)
    if clear:
        manager.clear_comms_loss(device_id)
        if isinstance(simulation, SimulationService):
            set_stabiliti_comms(simulation, device_id, ok=True)
        return {"status": "cleared", "device_id": device_id}
    manager.inject_comms_loss(device_id)
    if isinstance(simulation, SimulationService):
        set_stabiliti_comms(simulation, device_id, ok=False)
    return {"status": "injected", "device_id": device_id}


@router.websocket("/ems/ws")
async def ems_ws(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token")
    auth = getattr(websocket.app.state, "ems_auth", None)
    hub = getattr(websocket.app.state, "ems_hub", None)
    if not isinstance(auth, AuthService) or not isinstance(hub, EmsHub):
        await websocket.close(code=1013)
        return
    if not token:
        await websocket.close(code=1008)
        return
    try:
        auth.parse_token(token)
    except AuthError:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        async for message in hub.subscribe():
            await websocket.send_json(message)
    except (WebSocketDisconnect, RuntimeError):
        return
