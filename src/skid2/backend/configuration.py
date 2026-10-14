"""Review and apply settings without confusing stored intent with runtime state."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from skid2.backend.action_audit import AuditedActionRoute
from skid2.backend.auth import current_user, require_admin
from skid2.backend.monitor import manager
from skid2.backend.schemas import DeviceConfigRequest
from skid2.backend.simulator import InputRequest, ProfileRequest, _service
from skid2.persistence.accounts import User
from skid2.persistence.configuration import ConfigurationRepository
from skid2.protocol.client import load_soc
from skid2.settings import get_settings

Scope = Literal["monitor", "simulation"]
router = APIRouter(prefix="/api/v1/configuration", route_class=AuditedActionRoute)


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=1000)
    expected_generation: int = Field(ge=0, strict=True)


class ConnectionDraft(DraftRequest):
    settings: DeviceConfigRequest


class ProfileDraft(DraftRequest):
    settings: ProfileRequest


class InputsDraft(DraftRequest):
    inputs: InputRequest


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_generation: int = Field(ge=0, strict=True)
    restore: bool = False


class RevisionResponse(BaseModel):
    id: int
    scope: Scope
    name: str
    reason: str
    payload: dict[str, Any]
    created_by: str
    created_at: datetime
    approved_by: str | None = None
    approved_at: datetime | None = None


class HeadResponse(BaseModel):
    scope: Scope
    revision_id: int | None
    generation: int
    pending_action: str | None


class InstalledResponse(BaseModel):
    revision_id: int


class SettingsResponse(BaseModel):
    head: HeadResponse
    installed: InstalledResponse | None
    message: str
    revisions: list[RevisionResponse]
    next_before: int | None


class ActionResponse(BaseModel):
    id: str
    scope: str
    actor: str
    kind: str
    outcome: Literal[
        "pending", "accepted", "applied", "rejected", "failed", "timed_out", "interrupted"
    ]
    before: dict[str, Any] | None
    requested: dict[str, Any] | None
    effective: dict[str, Any] | None
    message: str
    created_at: datetime
    finished_at: datetime | None


class ChangeResponse(BaseModel):
    field: str
    before: Any
    after: Any


class ComparisonResponse(BaseModel):
    generation: int
    current_revision_id: int | None
    changes: list[ChangeResponse]


class AppliedResponse(BaseModel):
    outcome: Literal["applied"]
    revision_id: int
    generation: int


def repository(request: Request) -> ConfigurationRepository:
    store = getattr(request.app.state, "configuration", None)
    if not isinstance(store, ConfigurationRepository):
        raise HTTPException(503, "Settings history is unavailable. Try again shortly.")
    return store


def editor(user: Annotated[User, Depends(current_user)]) -> User:
    if user["role"] not in {"operator", "admin"}:
        raise HTTPException(403, "Operator access is required to propose settings.")
    return user


def validate_monitor(payload: dict[str, Any]) -> None:
    settings = get_settings()
    for value in payload["connections"].values():
        connection = DeviceConfigRequest.model_validate({**value, "revision": 0})
        path = (
            settings.live_bess_map
            if connection.mode == "live"
            else (settings.protocol_root / "devices/ba1-bess-v1.18.candidate.yaml")
        )
        if path is None:
            raise ValueError("An approved battery register map is required for live equipment.")
        load_soc(path, live=connection.mode == "live")


def validate(request: Request, revision: dict[str, Any]) -> None:
    if revision["scope"] == "monitor":
        validate_monitor(revision["payload"])
    else:
        _service(request).validate_configuration(revision["payload"])


@router.get("/{scope}", response_model=SettingsResponse)
def state(
    scope: Scope,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    before: Annotated[int | None, Query(ge=1)] = None,
) -> dict[str, Any]:
    store = repository(request)
    head = store.head(scope)
    installed: dict[str, Any] | None = None
    message = "No settings have been applied yet."
    if scope == "monitor":
        runtime = manager(request)
        if runtime.revision == head["generation"] and head["revision_id"] is not None:
            installed = {"revision_id": head["revision_id"]}
            message = (
                "Installed in the application. Check battery monitoring for device communication."
            )
    else:
        simulation = _service(request)
        if head["revision_id"] is not None:
            revision = store.revision(head["revision_id"])
            if simulation.configuration_matches(revision["payload"]):
                installed = {"revision_id": head["revision_id"]}
                message = "Installed for this simulation run."
            else:
                message = "This run uses a saved checkpoint or earlier setup. Start a new run to use the applied settings."
    if head["pending_action"]:
        installed = None
        message = "A change is awaiting completion. Reload to check its outcome; restart the backend if it was interrupted."
    history = store.history(scope, before)
    return {
        "head": head,
        "installed": installed,
        "message": message,
        "revisions": history,
        "next_before": history[-1]["id"] if len(history) == 50 else None,
    }


@router.get("/{scope}/activity", response_model=list[ActionResponse])
def activity(
    scope: Scope,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> list[dict[str, Any]]:
    return repository(request).audit(scope, offset)


@router.post("/monitor/drafts", response_model=RevisionResponse, status_code=201)
def connection_draft(
    body: ConnectionDraft, request: Request, user: Annotated[User, Depends(require_admin)]
) -> dict[str, Any]:
    store = repository(request)
    saved = manager(request).config.load()
    values = body.settings.model_dump(exclude={"revision"})
    key = f"{values['device_id']}:{values['mode']}"
    payload = {"active": key, "connections": {**saved["connections"], key: values}}
    try:
        validate_monitor(payload)
    except (ValueError, OSError):
        message = "Connection settings or the approved register map are invalid."
        store.record("monitor", user["username"], "save_draft", "rejected", message, values)
        raise HTTPException(422, message) from None
    return store.create(
        "monitor", body.name, body.reason, payload, user["username"], body.expected_generation
    )


@router.post("/simulation/profiles", response_model=RevisionResponse, status_code=201)
def profile_draft(
    body: ProfileDraft, request: Request, user: Annotated[User, Depends(editor)]
) -> dict[str, Any]:
    service = _service(request)
    settings = body.settings
    try:
        payload = service.propose_profile(
            settings.profile_id,
            settings.seed,
            battery_profile=settings.battery_profile,
            battery=settings.battery_settings.to_domain() if settings.battery_settings else None,
            bus=settings.bus_settings.to_domain() if settings.bus_settings else None,
        )
    except ValueError:
        message = "Check the simulation, battery and DC bus settings."
        repository(request).record(
            "simulation",
            user["username"],
            "save_draft",
            "rejected",
            message,
            body.settings.model_dump(),
        )
        raise HTTPException(422, message) from None
    return repository(request).create(
        "simulation", body.name, body.reason, payload, user["username"], body.expected_generation
    )


@router.post("/simulation/devices/{device_id}", response_model=RevisionResponse, status_code=201)
def input_draft(
    device_id: str, body: InputsDraft, request: Request, user: Annotated[User, Depends(editor)]
) -> dict[str, Any]:
    try:
        payload = _service(request).propose_inputs(device_id, body.inputs.to_domain())
    except ValueError:
        message = "Check the input values for the selected device."
        repository(request).record(
            "simulation",
            user["username"],
            "save_draft",
            "rejected",
            message,
            body.inputs.model_dump(),
        )
        raise HTTPException(422, message) from None
    return repository(request).create(
        "simulation", body.name, body.reason, payload, user["username"], body.expected_generation
    )


@router.post("/revisions/{revision_id}/approve", response_model=RevisionResponse)
def approve(
    revision_id: int, request: Request, user: Annotated[User, Depends(require_admin)]
) -> dict[str, Any]:
    store = repository(request)
    revision = store.revision(revision_id)
    try:
        validate(request, revision)
    except (ValueError, OSError):
        message = "These settings no longer pass validation. Save a corrected proposal."
        store.record(
            revision["scope"],
            user["username"],
            "approve",
            "rejected",
            message,
            {"revision_id": revision_id},
        )
        raise HTTPException(422, message) from None
    store.approve(revision_id, user["username"])
    return store.revision(revision_id)


@router.post("/revisions/{revision_id}/apply", response_model=AppliedResponse)
async def apply(
    revision_id: int,
    body: ApplyRequest,
    request: Request,
    user: Annotated[User, Depends(require_admin)],
) -> dict[str, Any]:
    store = repository(request)
    # One application process owns the physical readers and simulation. The database
    # pending marker also serializes competing requests before any runtime mutation.
    async with request.app.state.configuration_lock:
        action = store.begin(
            revision_id,
            body.expected_generation,
            user["username"],
            "restore" if body.restore else "apply",
        )
        revision = action["revision"]
        try:
            validate(request, revision)
            if revision["scope"] == "monitor":
                await manager(request).apply(
                    {**revision["payload"], "revision": action["generation"]}
                )
            else:
                _service(request).install_configuration(revision["payload"])
        except (ValueError, OSError):
            message = "Settings were not applied. Pause the simulation and check that the settings are still valid."
            store.finish(action, message)
            raise HTTPException(409, message) from None
        # A failed commit deliberately leaves durable intent pending. Do not report
        # success or retry runtime effects; restart reconciles against the projection.
        store.finish(action)
        return {
            "outcome": "applied",
            "revision_id": revision_id,
            "generation": action["generation"],
        }


@router.get("/revisions/{revision_id}/compare", response_model=ComparisonResponse)
def compare(
    revision_id: int, request: Request, user: Annotated[User, Depends(current_user)]
) -> dict[str, Any]:
    store = repository(request)
    revision = store.revision(revision_id)
    head = store.head(revision["scope"])
    current = store.revision(head["revision_id"])["payload"] if head["revision_id"] else {}

    def flatten(value: object, prefix: str = "") -> dict[str, Any]:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, child in value.items():
                result.update(flatten(child, f"{prefix}.{key}" if prefix else key))
            return result
        return {prefix: value}

    old, new = flatten(current), flatten(revision["payload"])
    return {
        "generation": head["generation"],
        "current_revision_id": head["revision_id"],
        "changes": [
            {"field": key, "before": old.get(key), "after": new.get(key)}
            for key in sorted(old.keys() | new.keys())
            if old.get(key) != new.get(key)
        ],
    }
