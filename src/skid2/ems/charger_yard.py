"""Six-port charger SIM. Internal sat caps 600/300/300 kW; PMS sees the aggregate only."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

PORT_IDS: tuple[str, ...] = ("1", "2", "3", "4", "5", "6")
PORT_LABELS: dict[str, str] = {
    "1": "S1-A",
    "2": "S1-B",
    "3": "S2-A",
    "4": "S2-B",
    "5": "S3-A",
    "6": "S3-B",
}
MIN_REQUEST_KW = 30.0
MAX_REQUEST_KW = 600.0
CABLE_MAX_KW = 375.0
PORT_SATELLITE: dict[str, str] = {
    "1": "sat1",
    "2": "sat1",
    "3": "sat2",
    "4": "sat2",
    "5": "sat3",
    "6": "sat3",
}
SATELLITE_CAP_KW: dict[str, float] = {"sat1": 600.0, "sat2": 300.0, "sat3": 300.0}


@dataclass(frozen=True, slots=True)
class PortState:
    port_id: str
    label: str
    occupied: bool
    requested_kw: float
    allocated_kw: float
    energy_kwh: float = 0.0
    session_id: int | None = None
    capped: bool = False


@dataclass(frozen=True, slots=True)
class YardSnapshot:
    ports: tuple[PortState, ...]
    demand_kw: float
    available_kw: float
    unmet_kw: float
    events: tuple[str, ...]


@dataclass
class ChargerYard:
    """Field-style EV sessions: plug in, ask for power, plant checks availability."""

    _occupied: dict[str, bool] = field(default_factory=dict)
    _requested: dict[str, float] = field(default_factory=dict)
    _arrival_order: list[str] = field(default_factory=list)
    _events: list[str] = field(default_factory=list)
    _energy: dict[str, float] = field(default_factory=dict)
    _session_ids: dict[str, int | None] = field(default_factory=dict)
    _capped: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for port_id in PORT_IDS:
            self._occupied.setdefault(port_id, False)
            self._requested.setdefault(port_id, 0.0)
            self._energy.setdefault(port_id, 0.0)
            self._session_ids.setdefault(port_id, None)
            self._capped.setdefault(port_id, False)

    def snapshot(self, available_kw: float, *, allow_new: bool) -> YardSnapshot:
        allocated = self._allocate(max(0.0, available_kw), allow_new)
        ports = tuple(self._port(port_id, allocated) for port_id in PORT_IDS)
        demand = sum(port.requested_kw for port in ports)
        served = sum(port.allocated_kw for port in ports)
        return YardSnapshot(
            ports,
            demand,
            max(0.0, available_kw),
            max(0.0, demand - served),
            tuple(self._events[-20:]),
        )

    def set_port(
        self,
        port_id: str,
        *,
        occupied: bool,
        requested_kw: float,
        allow_new: bool,
    ) -> PortState:
        if port_id not in PORT_IDS:
            raise KeyError(f"unknown port {port_id}")
        if occupied and not self._occupied[port_id] and not allow_new:
            raise ValueError("new sessions are blocked by charger SOC gates")
        if occupied:
            if requested_kw < MIN_REQUEST_KW or requested_kw > MAX_REQUEST_KW:
                raise ValueError(f"request must be {MIN_REQUEST_KW:.0f}-{MAX_REQUEST_KW:.0f} kW")
        else:
            requested_kw = 0.0
        was = self._occupied[port_id]
        self._occupied[port_id] = occupied
        self._requested[port_id] = requested_kw if occupied else 0.0
        if occupied and port_id not in self._arrival_order:
            self._arrival_order.append(port_id)
            self._energy[port_id] = 0.0
            self._capped[port_id] = False
        if not occupied and port_id in self._arrival_order:
            self._arrival_order.remove(port_id)
            self._session_ids[port_id] = None
            self._capped[port_id] = False
        stamp = datetime.now(tz=UTC).strftime("%H:%M:%S")
        if occupied and not was:
            self._events.append(
                f"{stamp} car plugged into port {port_id} ({PORT_LABELS[port_id]}) asking {requested_kw:.0f} kW"
            )
        elif occupied and was:
            self._events.append(f"{stamp} port {port_id} request changed to {requested_kw:.0f} kW")
        elif was and not occupied:
            self._events.append(f"{stamp} car left port {port_id} ({PORT_LABELS[port_id]})")
        allocated = self._allocate(0.0, allow_new)
        return self._port(port_id, allocated)

    def restore_port(
        self,
        port_id: str,
        *,
        requested_kw: float,
        energy_kwh: float,
        session_id: int,
        capped: bool,
    ) -> None:
        if port_id not in PORT_IDS:
            raise KeyError(f"unknown port {port_id}")
        clamped = min(max(requested_kw, MIN_REQUEST_KW), MAX_REQUEST_KW)
        self._occupied[port_id] = True
        self._requested[port_id] = clamped
        self._energy[port_id] = max(0.0, energy_kwh)
        self._session_ids[port_id] = session_id
        self._capped[port_id] = capped
        if port_id not in self._arrival_order:
            self._arrival_order.append(port_id)

    def bind_session(self, port_id: str, session_id: int) -> None:
        if port_id not in PORT_IDS:
            raise KeyError(f"unknown port {port_id}")
        self._session_ids[port_id] = session_id

    def tick(
        self,
        dt_seconds: float,
        available_kw: float,
        *,
        allow_new: bool,
        energy_cap_kwh: float | None,
    ) -> YardSnapshot:
        if dt_seconds < 0:
            raise ValueError("dt must not be negative")
        snap = self.snapshot(available_kw, allow_new=allow_new)
        for port in snap.ports:
            if not port.occupied:
                continue
            added = port.allocated_kw * dt_seconds / 3600.0
            self._energy[port.port_id] = port.energy_kwh + added
            if energy_cap_kwh is not None and self._energy[port.port_id] >= energy_cap_kwh:
                if not self._capped[port.port_id]:
                    stamp = datetime.now(tz=UTC).strftime("%H:%M:%S")
                    self._events.append(
                        f"{stamp} port {port.port_id} hit session cap {energy_cap_kwh:.0f} kWh"
                    )
                self._capped[port.port_id] = True
        return self.snapshot(available_kw, allow_new=allow_new)

    def _port(self, port_id: str, allocated: dict[str, float]) -> PortState:
        occupied = self._occupied[port_id]
        return PortState(
            port_id,
            PORT_LABELS[port_id],
            occupied,
            self._requested[port_id] if occupied else 0.0,
            0.0 if self._capped[port_id] else allocated.get(port_id, 0.0),
            self._energy[port_id] if occupied else 0.0,
            self._session_ids[port_id] if occupied else None,
            self._capped[port_id] if occupied else False,
        )

    def _allocate(self, available_kw: float, _allow_new: bool) -> dict[str, float]:
        remaining = max(0.0, available_kw)
        allocated = dict.fromkeys(PORT_IDS, 0.0)
        sat_left = dict(SATELLITE_CAP_KW)
        active = [
            port_id
            for port_id in self._arrival_order
            if self._occupied[port_id] and not self._capped[port_id]
        ]
        if not active or remaining <= 0:
            return allocated
        share = remaining / len(active)
        for port_id in active:
            sat = PORT_SATELLITE[port_id]
            give = min(
                self._requested[port_id],
                sat_left[sat],
                CABLE_MAX_KW,
                remaining,
                share,
            )
            give = max(0.0, give)
            allocated[port_id] = give
            sat_left[sat] -= give
            remaining -= give
        for port_id in active:
            if remaining <= 0:
                break
            sat = PORT_SATELLITE[port_id]
            room = min(
                self._requested[port_id] - allocated[port_id],
                sat_left[sat],
                CABLE_MAX_KW - allocated[port_id],
            )
            extra = min(max(0.0, room), remaining)
            allocated[port_id] += extra
            sat_left[sat] -= extra
            remaining -= extra
        return allocated
