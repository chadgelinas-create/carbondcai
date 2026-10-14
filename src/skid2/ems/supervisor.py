"""Whole-skid supervisory calculations and alarm engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy import Engine
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from skid2.ems.charger_envelope import ChargerEnvelope, EnvelopeDecision
from skid2.ems.config import ConfigService
from skid2.ems.groups import DEVICE_IDS
from skid2.ems.hub import EmsHub, LivePoint
from skid2.persistence.metadata import ems_alarm, ems_event


@dataclass(frozen=True, slots=True)
class SupervisorSnapshot:
    bus_voltage: float
    system_status: int
    available_power_kw: float
    heartbeats_ok: int
    heartbeats_total: int
    status_label: str
    charger_status: int = 0
    charger_status_label: str = "normal"
    charger_time_remaining_min: float = 999.0
    charger_pv_kw: float = 0.0


class Supervisor:
    """Cross-cutting EMS supervisor for Week 6 demos."""

    STATUS_LABELS: ClassVar[dict[int, str]] = {
        0: "Offline",
        1: "Initializing",
        2: "Standby",
        3: "Running",
        4: "Degraded",
        5: "Fault",
    }

    def __init__(self, hub: EmsHub, config: ConfigService, engine: Engine | None = None) -> None:
        self._hub = hub
        self._config = config
        self._engine = engine
        self._envelope = ChargerEnvelope()

    def snapshot(self) -> SupervisorSnapshot:
        points = {point.point_id: point for point in self._hub.snapshot()}
        env = self._envelope_from(points, dt_seconds=0.0)
        ok = sum(1 for device_id in DEVICE_IDS if self._hub.heartbeats.get(device_id, False))
        total = len(DEVICE_IDS)
        status = self._status_code(ok, total, points)
        bus = _value(
            points,
            "plant.bus.voltage",
            _numeric(self._config, "supervisor", "bus_voltage_setpoint", 750.0),
        )
        return SupervisorSnapshot(
            bus_voltage=bus,
            system_status=status,
            available_power_kw=env.available_kw,
            heartbeats_ok=ok,
            heartbeats_total=total,
            status_label=self.STATUS_LABELS[status],
            charger_status=env.status,
            charger_status_label=env.status_label,
            charger_time_remaining_min=env.time_remaining_min,
            charger_pv_kw=env.pv_kw,
        )

    def advance_envelope(self, dt_seconds: float) -> EnvelopeDecision:
        points = {point.point_id: point for point in self._hub.snapshot()}
        return self._envelope_from(points, dt_seconds=dt_seconds)

    def _envelope_from(self, points: dict[str, LivePoint], *, dt_seconds: float) -> EnvelopeDecision:
        bus = _value(
            points,
            "plant.bus.voltage",
            _numeric(self._config, "supervisor", "bus_voltage_setpoint", 750.0),
        )
        pv = _value(points, "cnvtr-2.accepted.power", 0.0) + _value(points, "plant.pv.power", 0.0)
        soc = _value(points, "bess.cluster.soc", 0.5)
        bess_limit = _value(points, "bess.cluster.limit.discharge.power", 0.0)
        if bess_limit <= 0:
            bess_limit = max(0.0, -_value(points, "bess.cluster.current", 0.0) * bus / 1000.0)
        cap = _numeric(self._config, "supervisor", "bus_power_cap", 600.0)
        if bess_limit <= 0 and soc > _numeric(self._config, "supervisor", "bess_reserve_soc", 0.075):
            bess_limit = cap
        return self._envelope.compute(
            pv_kw=pv,
            soc=soc,
            bess_limit_kw=bess_limit,
            bus_v=bus,
            comms_ok=self._hub.heartbeats.get("dc600", True),
            charger_fault=_value(points, "dc600.fault.active", 0.0) >= 0.5,
            dt_seconds=dt_seconds,
            config=self._config,
        )

    def _status_code(self, ok: int, total: int, points: dict[str, LivePoint]) -> int:
        if ok == 0:
            return 0
        if ok < total:
            return 4
        if any(point.stale for point in points.values()):
            return 4
        soc = _value(points, "bess.cluster.soc", 0.5)
        terminate = _numeric(self._config, "charger", "soc_terminate", 0.10)
        unavailable = _numeric(self._config, "charger", "soc_maintenance", 0.15)
        if soc <= terminate:
            return 5
        if soc <= unavailable:
            return 4
        return 3

    def evaluate_alarms(self) -> list[dict[str, str | bool]]:
        snap = self.snapshot()
        points = {point.point_id: point for point in self._hub.snapshot()}
        soc = _value(points, "bess.cluster.soc", 0.5)
        raised: list[dict[str, str | bool]] = []
        if snap.heartbeats_ok < snap.heartbeats_total:
            raised.append(
                self._raise(
                    "comms.partial",
                    f"Device heartbeats {snap.heartbeats_ok}/{snap.heartbeats_total}",
                    "warning",
                )
            )
        else:
            self._clear("comms.partial")
        terminate = _numeric(self._config, "charger", "soc_terminate", 0.10)
        unavailable = _numeric(self._config, "charger", "soc_maintenance", 0.15)
        if soc <= terminate:
            raised.append(
                self._raise("bess.soc.critical", "BESS SOC at terminate threshold", "critical")
            )
        else:
            self._clear("bess.soc.critical")
        if soc <= unavailable:
            raised.append(self._raise("bess.soc.low", "BESS SOC in limited band", "warning"))
        else:
            self._clear("bess.soc.low")
        for unit in ("st-4", "st-5", "st-6"):
            code = _value(points, f"{unit}.operational.state", -1.0)
            if not self._hub.heartbeats.get(unit, True) or code == 0:
                raised.append(
                    self._raise(
                        f"{unit}.offline",
                        f"{unit.upper()} offline or communication lost",
                        "warning",
                    )
                )
            else:
                self._clear(f"{unit}.offline")
            if _value(points, f"{unit}.derated", 0.0) >= 0.5 or code == 3:
                raised.append(self._raise(f"{unit}.derated", f"{unit.upper()} derated", "warning"))
            else:
                self._clear(f"{unit}.derated")
        return raised

    def record_event(self, category: str, message: str, device_id: str | None = None) -> None:
        if self._engine is None:
            return
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(ems_event).values(
                        category=category,
                        message=message,
                        device_id=device_id,
                        occurred_at=datetime.now(tz=UTC),
                        synthetic=True,
                    )
                )
        except SQLAlchemyError:
            return

    def _raise(self, alarm_id: str, message: str, severity: str) -> dict[str, str | bool]:
        if self._engine is not None:
            try:
                with self._engine.begin() as connection:
                    connection.execute(
                        insert(ems_alarm)
                        .values(
                            alarm_id=alarm_id,
                            message=message,
                            severity=severity,
                            device_id=None,
                            active=True,
                            occurred_at=datetime.now(tz=UTC),
                            cleared_at=None,
                            synthetic=True,
                        )
                        .on_conflict_do_update(
                            index_elements=["alarm_id"],
                            set_={
                                "message": message,
                                "severity": severity,
                                "active": True,
                                "cleared_at": None,
                            },
                        )
                    )
            except SQLAlchemyError:
                pass
        return {"alarm_id": alarm_id, "message": message, "severity": severity, "active": True}

    def _clear(self, alarm_id: str) -> None:
        if self._engine is None:
            return
        try:
            from sqlalchemy import update

            with self._engine.begin() as connection:
                connection.execute(
                    update(ems_alarm)
                    .where(ems_alarm.c.alarm_id == alarm_id, ems_alarm.c.active.is_(True))
                    .values(active=False, cleared_at=datetime.now(tz=UTC))
                )
        except SQLAlchemyError:
            return


def _value(points: dict[str, LivePoint], point_id: str, default: float) -> float:
    point = points.get(point_id)
    if point is None or point.stale:
        return default
    return point.value


def _numeric(config: ConfigService, group_id: str, key: str, default: float) -> float:
    try:
        return float(config.numeric(group_id, key))
    except (KeyError, TypeError, ValueError, AttributeError):
        return default
