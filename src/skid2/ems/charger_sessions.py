"""Persisted charger session ledger for the EMS yard."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True)
class SessionRecord:
    id: int
    port_id: str
    status: str
    energy_kwh: float
    power_kw: float
    capped: bool
    started_at: datetime
    ended_at: datetime | None = None


@dataclass
class ChargerSessionStore:
    """Session open/close store. Engine is optional for lab bootstrapping."""

    _engine: Any = None
    _next_id: int = 1
    _rows: list[SessionRecord] = field(default_factory=list)
    _events: list[str] = field(default_factory=list)

    def __init__(self, engine: Any | None = None) -> None:
        self._engine = engine
        self._next_id = 1
        self._rows = []
        self._events = []

    def restore_into(self, yard: Any) -> None:
        """Re-bind active sessions onto yard ports after process restart."""

        for row in self._rows:
            if row.status != "active":
                continue
            try:
                yard.set_port(
                    row.port_id,
                    occupied=True,
                    requested_kw=row.power_kw,
                    allow_new=True,
                )
                yard.bind_session(row.port_id, row.id)
            except (KeyError, ValueError):
                continue

    def open_session(self, port_id: str, power_kw: float) -> SessionRecord:
        record = SessionRecord(
            id=self._next_id,
            port_id=port_id,
            status="active",
            energy_kwh=0.0,
            power_kw=float(power_kw),
            capped=False,
            started_at=datetime.now(tz=UTC),
        )
        self._next_id += 1
        self._rows.append(record)
        self._events.append(f"session {record.id} opened on {port_id}")
        return record

    def close_session(self, port_id: str, energy_kwh: float, *, capped: bool) -> None:
        for row in reversed(self._rows):
            if row.port_id == port_id and row.status == "active":
                row.status = "ended"
                row.energy_kwh = float(energy_kwh)
                row.capped = capped
                row.ended_at = datetime.now(tz=UTC)
                self._events.append(f"session {row.id} closed on {port_id}")
                return

    def recent(self, limit: int = 20) -> list[SessionRecord]:
        return list(reversed(self._rows[-limit:]))

    def events(self) -> list[str]:
        return list(self._events)
