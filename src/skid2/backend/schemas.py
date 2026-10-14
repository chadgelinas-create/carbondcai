"""Typed API representations mapped explicitly from domain contracts."""

from __future__ import annotations

import ipaddress
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from skid2.application.runtime import RuntimeHealth
from skid2.contracts import (
    ComponentStatus,
    DataQuality,
    Freshness,
    Heartbeat,
    Measurement,
    SystemSnapshot,
)


class MeasurementResponse(BaseModel):
    """Public measurement shape preserving all Phase 2 semantics."""

    point_id: str
    label: str
    source: str
    value: float
    unit: str
    source_timestamp: datetime
    receive_timestamp: datetime
    quality: DataQuality
    synthetic: bool

    @classmethod
    def from_domain(cls, measurement: Measurement) -> MeasurementResponse:
        """Map the single domain contract to its API representation."""

        return cls(
            point_id=measurement.point_id,
            label=measurement.label,
            source=measurement.source,
            value=measurement.value,
            unit=measurement.unit,
            source_timestamp=measurement.source_timestamp,
            receive_timestamp=measurement.receive_timestamp,
            quality=measurement.quality,
            synthetic=measurement.synthetic,
        )


class HeartbeatResponse(BaseModel):
    """Public component heartbeat."""

    component: str
    observed_at: datetime
    status: ComponentStatus
    synthetic: bool

    @classmethod
    def from_domain(cls, heartbeat: Heartbeat) -> HeartbeatResponse:
        """Map a heartbeat without making the API schema domain authority."""

        return cls(
            component=heartbeat.component,
            observed_at=heartbeat.observed_at,
            status=heartbeat.status,
            synthetic=heartbeat.synthetic,
        )


class CurrentStateResponse(BaseModel):
    """Versioned current-state response."""

    observed_at: datetime
    freshness: Freshness
    measurement: MeasurementResponse | None
    heartbeat: HeartbeatResponse | None

    @classmethod
    def from_domain(cls, snapshot: SystemSnapshot) -> CurrentStateResponse:
        """Map current domain state to the wire boundary."""

        return cls(
            observed_at=snapshot.observed_at,
            freshness=snapshot.freshness,
            measurement=(
                None
                if snapshot.measurement is None
                else MeasurementResponse.from_domain(snapshot.measurement)
            ),
            heartbeat=(
                None
                if snapshot.heartbeat is None
                else HeartbeatResponse.from_domain(snapshot.heartbeat)
            ),
        )


class ComponentHealthResponse(BaseModel):
    """Bounded health for each walking-skeleton boundary."""

    status: Literal["ready", "degraded"]
    database: ComponentStatus
    migration: Literal["current"]
    synthetic_source: ComponentStatus
    current_state: Freshness
    historian: ComponentStatus

    @classmethod
    def from_runtime(cls, health: RuntimeHealth) -> ComponentHealthResponse:
        """Combine healthy database/migration state with runtime state."""

        overall: Literal["ready", "degraded"] = (
            "ready"
            if health.historian is ComponentStatus.HEALTHY
            and health.current_state is not Freshness.UNAVAILABLE
            else "degraded"
        )
        return cls(
            status=overall,
            database=ComponentStatus.HEALTHY,
            migration="current",
            synthetic_source=health.synthetic_source,
            current_state=health.current_state,
            historian=health.historian,
        )


class DeviceConfigRequest(BaseModel):
    """Validate the selected battery connection."""

    model_config = ConfigDict(extra="forbid", strict=True)
    device_id: Literal["ba1-1"]
    mode: Literal["sim", "live"]
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    unit_id: int = Field(ge=1, le=247)
    poll_seconds: float = Field(ge=0.1, le=3600, allow_inf_nan=False)
    timeout_seconds: float = Field(ge=0.1, le=60, allow_inf_nan=False)
    stale_seconds: float = Field(ge=0.2, le=86400, allow_inf_nan=False)
    revision: int = Field(ge=0)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        try:
            ipaddress.ip_address(value)
        except ValueError:
            labels = value.split(".")
            if not all(
                re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                for label in labels
            ):
                raise ValueError(
                    "Enter a device IP address or hostname, without a URL or port"
                ) from None
        return value

    @model_validator(mode="after")
    def validate_timing(self) -> DeviceConfigRequest:
        if self.stale_seconds <= self.poll_seconds + self.timeout_seconds:
            raise ValueError("Stale time must exceed the read interval plus connection timeout")
        return self
