"""Persist charging-session lifecycle onto the existing ev_session table."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import Engine, select, update
from sqlalchemy.exc import SQLAlchemyError

from skid2.ems.charger_yard import PORT_IDS, ChargerYard, YardSnapshot
from skid2.persistence.metadata import ems_event, ev_session


@dataclass(frozen=True, slots=True)
class SessionRecord:
    id: int
    port_id: str
    status: str
    energy_kwh: float
    power_kw: float
    capped: bool
    started_at: datetime
    ended_at: datetime | None


@dataclass
class ChargerSessionStore:
    """Memory first; Postgres when the EMS engine is attached."""

    _engine: Engine | None = None
    _next_id: int = 1
    _open: dict[str, SessionRecord] = field(default_factory=dict)
    _history: list[SessionRecord] = field(default_factory=list)
    _events: list[str] = field(default_factory=list)

    def open_session(self, port_id: str, requested_kw: float) -> SessionRecord:
        if port_id in self._open:
            return self._open[port_id]
        now = datetime.now(tz=UTC)
        db_id = self._insert_row(port_id, requested_kw, now)
        record = SessionRecord(
            db_id if db_id is not None else self._allocate_id(),
            port_id,
            "charging",
            0.0,
            requested_kw,
            False,
            now,
            None,
        )
        self._open[port_id] = record
        self._note(f"session {record.id} started on port {port_id} at {requested_kw:.0f} kW")
        return record

    def update_open(self, snapshot: YardSnapshot) -> None:
        for port in snapshot.ports:
            current = self._open.get(port.port_id)
            if current is None or not port.occupied:
                continue
            limited = port.allocated_kw + 0.5 < port.requested_kw
            status = "capped" if port.capped else "limited" if limited else "charging"
            updated = SessionRecord(
                current.id,
                current.port_id,
                status,
                port.energy_kwh,
                current.power_kw,
                port.capped,
                current.started_at,
                None,
            )
            self._open[port.port_id] = updated
            self._write_update(updated)

    def close_session(
        self, port_id: str, energy_kwh: float, *, capped: bool
    ) -> SessionRecord | None:
        current = self._open.pop(port_id, None)
        if current is None:
            return None
        finished = SessionRecord(
            current.id,
            current.port_id,
            "ended",
            energy_kwh,
            current.power_kw,
            capped or current.capped,
            current.started_at,
            datetime.now(tz=UTC),
        )
        self._history.append(finished)
        self._note(f"session {finished.id} ended on port {port_id} {finished.energy_kwh:.2f} kWh")
        self._write_update(finished)
        return finished

    def restore_into(self, yard: ChargerYard) -> None:
        for record in self._load_open():
            self._open[record.port_id] = record
            self._next_id = max(self._next_id, record.id + 1)
            try:
                yard.restore_port(
                    record.port_id,
                    requested_kw=record.power_kw,
                    energy_kwh=record.energy_kwh,
                    session_id=record.id,
                    capped=record.capped,
                )
            except (KeyError, ValueError):
                continue

    def open_records(self) -> tuple[SessionRecord, ...]:
        return tuple(self._open[port_id] for port_id in PORT_IDS if port_id in self._open)

    def recent(self, limit: int = 20) -> tuple[SessionRecord, ...]:
        closed = tuple(reversed(self._history[-limit:]))
        return self.open_records() + closed

    def events(self) -> tuple[str, ...]:
        return tuple(self._events[-40:])

    def _allocate_id(self) -> int:
        allocated = self._next_id
        self._next_id += 1
        return allocated

    def _note(self, message: str) -> None:
        stamp = datetime.now(tz=UTC).strftime("%H:%M:%S")
        line = f"{stamp} {message}"
        self._events.append(line)
        if self._engine is None:
            return
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    ems_event.insert().values(
                        category="charger.session",
                        message=message,
                        device_id="dc600",
                        occurred_at=datetime.now(tz=UTC),
                        synthetic=True,
                    )
                )
        except SQLAlchemyError:
            return

    def _insert_row(self, port_id: str, requested_kw: float, started_at: datetime) -> int | None:
        if self._engine is None:
            return None
        try:
            with self._engine.begin() as connection:
                result = connection.execute(
                    ev_session.insert()
                    .values(
                        port_id=port_id,
                        status="charging",
                        energy_kwh=0.0,
                        power_kw=requested_kw,
                        capped=False,
                        started_at=started_at,
                        ended_at=None,
                    )
                    .returning(ev_session.c.id)
                )
                return int(result.scalar_one())
        except SQLAlchemyError:
            return None

    def _write_update(self, record: SessionRecord) -> None:
        if self._engine is None:
            return
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    update(ev_session)
                    .where(ev_session.c.id == record.id)
                    .values(
                        status=record.status,
                        energy_kwh=record.energy_kwh,
                        power_kw=record.power_kw,
                        capped=record.capped,
                        ended_at=record.ended_at,
                    )
                )
        except SQLAlchemyError:
            return

    def _load_open(self) -> tuple[SessionRecord, ...]:
        if self._engine is None:
            return ()
        try:
            with self._engine.connect() as connection:
                rows = (
                    connection.execute(select(ev_session).where(ev_session.c.ended_at.is_(None)))
                    .mappings()
                    .all()
                )
        except SQLAlchemyError:
            return ()
        return tuple(
            SessionRecord(
                int(row["id"]),
                str(row["port_id"]),
                str(row["status"]),
                float(row["energy_kwh"]),
                float(row["power_kw"]),
                bool(row["capped"]),
                row["started_at"],
                row["ended_at"],
            )
            for row in rows
        )
