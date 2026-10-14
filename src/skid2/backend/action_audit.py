"""Durable intent and outcome for configuration and simulator mutations."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Coroutine
from typing import Annotated, Any, get_args, get_origin

from fastapi import HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel

from skid2.backend.auth import current_user
from skid2.persistence.configuration import ConfigurationConflictError, ConfigurationRepository
from skid2.settings import get_settings


def run_summary(request: Request) -> dict[str, Any] | None:
    service = getattr(request.app.state, "simulation", None)
    if service is None:
        return None
    state = service.state()
    return {
        "step": state.sequence,
        "lifecycle": state.lifecycle.value,
        "simulation_at": state.at.isoformat(),
        "trace_hash": state.trace_hash,
    }


def _models(annotation: object) -> list[type[BaseModel]]:
    if get_origin(annotation) is Annotated:
        return _models(get_args(annotation)[0])
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [model for child in get_args(annotation) for model in _models(child)]


def _submitted(annotation: object, value: object) -> object:
    """Retain invalid values without retaining arbitrary nested objects or credentials.

    Request models are the allowlist, not validation: e.g. a negative port remains
    visible as submitted. Unknown keys are dropped at every model boundary.
    Wrong container types and excessive scalar values get explicit bounded markers.
    """
    models = _models(annotation)
    if models and isinstance(value, dict):
        fields = {key: field for model in models for key, field in model.model_fields.items()}
        return {
            key: _submitted(field.annotation, value[key])
            for key, field in fields.items()
            if key in value
        }
    if isinstance(value, dict | list):
        return {"omitted_type": type(value).__name__}
    if isinstance(value, str) and len(value) > 1024:
        return {"prefix": value[:1024], "truncated": True, "length": len(value)}
    if isinstance(value, float) and not math.isfinite(value):
        return {"invalid_number": str(value)}
    if isinstance(value, int) and value.bit_length() > 256:
        return {"omitted_type": "oversized_integer"}
    return value


class AuditedActionRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def audited(request: Request) -> Response:
            store = getattr(request.app.state, "configuration", None)
            if request.method in {"GET", "HEAD", "OPTIONS"} or not isinstance(
                store, ConfigurationRepository
            ):
                return await handler(request)
            legacy_connection = request.url.path == "/api/v1/config"
            configuration = legacy_connection or request.url.path.startswith(
                "/api/v1/configuration/"
            )
            scope = "simulation"
            if legacy_connection:
                scope = "monitor"
            elif configuration:
                parts = request.url.path.split("/")
                scope = parts[4]
                if scope == "revisions":
                    try:
                        scope = store.revision(int(parts[5]))["scope"]
                    except (ValueError, IndexError):
                        scope = "configuration"

            def summary() -> dict[str, Any] | None:
                return store.head(scope) if configuration else run_summary(request)

            data = await request.body()
            try:
                parsed = json.loads(data) if data else {}
                requested = (
                    _submitted(self.body_field.field_info.annotation, parsed)
                    if self.body_field is not None
                    else {}
                )
            except (ValueError, RecursionError):
                requested = {"invalid_body": True}
            intent = {"path": request.url.path, "values": requested}
            try:
                actor = (
                    current_user(request)["username"]
                    if configuration or get_settings().auth_enabled
                    else "system:local"
                )
            except HTTPException:
                store.record(
                    scope,
                    "system:unverified_request",
                    "access",
                    "rejected",
                    "Sign-in or request verification was not accepted.",
                    intent,
                    summary(),
                    summary(),
                )
                raise
            if not configuration and store.head("simulation")["pending_action"]:
                store.record(
                    scope,
                    actor,
                    "command",
                    "rejected",
                    "Wait for the settings change to finish.",
                    intent,
                    summary(),
                    summary(),
                )
                raise HTTPException(
                    409, "Wait for the settings change to finish before changing this run."
                )
            action_id = store.record(
                scope,
                actor,
                ("request_" if configuration else "") + request.url.path.rsplit("/", 1)[-1],
                "pending",
                "Settings request received." if configuration else "Simulation action received.",
                intent,
                summary(),
            )
            try:
                response = await handler(request)
            except (HTTPException, RequestValidationError, ConfigurationConflictError) as error:
                store.finish_command(
                    action_id,
                    "rejected",
                    "Action rejected. Check the request and access permissions.",
                    summary(),
                )
                raise error
            except TimeoutError:
                store.finish_command(
                    action_id,
                    "timed_out",
                    "The action timed out. Check the current run before retrying.",
                    summary(),
                )
                raise
            except Exception:
                store.finish_command(
                    action_id,
                    "failed",
                    "The action failed. Check the current run before retrying.",
                    summary(),
                )
                raise
            store.finish_command(
                action_id,
                "accepted" if response.status_code < 400 else "rejected",
                "Request completed." if response.status_code < 400 else "Request rejected.",
                summary(),
            )
            return response

        return audited
