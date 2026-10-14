"""In-memory DC600 charger yard (ports, allocation, events)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class PortState:
    port_id: str
    label: str
    occupied: bool = False
    requested_kw: float = 0.0
    allocated_kw: float = 0.0
    energy_kwh: float = 0.0
    session_id: int | None = None
    capped: bool = False


@dataclass(frozen=True, slots=True)
class YardSnapshot:
    demand_kw: float
    available_kw: float
    unmet_kw: float
    ports: tuple[PortState, ...]
    events: tuple[str, ...]


@dataclass
class ChargerYard:
    """Allocates available skid power across occupied charger ports."""

    _ports: dict[str, PortState] = field(default_factory=dict)
    _events: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self._ports:
            for index in range(1, 5):
                port_id = f"port-{index}"
                self._ports[port_id] = PortState(port_id=port_id, label=f"Port {index}")

    @property
    def events(self) -> list[str]:
        return list(self._events)

    def snapshot(self, available_kw: float, *, allow_new: bool) -> YardSnapshot:
        del allow_new  # gating is enforced in set_port
        demand = sum(port.requested_kw for port in self._ports.values() if port.occupied)
        remaining = max(0.0, float(available_kw))
        ports: list[PortState] = []
        for port in self._ports.values():
            allocated = 0.0
            capped = False
            if port.occupied and port.requested_kw > 0:
                allocated = min(port.requested_kw, remaining)
                capped = allocated < port.requested_kw
                remaining -= allocated
            updated = PortState(
                port_id=port.port_id,
                label=port.label,
                occupied=port.occupied,
                requested_kw=port.requested_kw,
                allocated_kw=allocated,
                energy_kwh=port.energy_kwh,
                session_id=port.session_id,
                capped=capped,
            )
            port.allocated_kw = allocated
            port.capped = capped
            ports.append(updated)
        unmet = max(0.0, demand - float(available_kw))
        return YardSnapshot(
            demand_kw=demand,
            available_kw=float(available_kw),
            unmet_kw=unmet,
            ports=tuple(ports),
            events=tuple(self._events[-20:]),
        )

    def set_port(
        self,
        port_id: str,
        *,
        occupied: bool,
        requested_kw: float,
        allow_new: bool,
    ) -> None:
        port = self._ports.get(port_id)
        if port is None:
            raise KeyError(f"unknown port {port_id}")
        if occupied and not port.occupied and not allow_new:
            raise ValueError("new charger sessions are not allowed")
        if requested_kw < 0:
            raise ValueError("requested_kw must be >= 0")
        port.occupied = occupied
        port.requested_kw = float(requested_kw) if occupied else 0.0
        if not occupied:
            port.allocated_kw = 0.0
            port.session_id = None
            port.capped = False
        self._events.append(
            f"{port_id} occupied={occupied} request={port.requested_kw:g} kW"
        )

    def bind_session(self, port_id: str, session_id: int) -> None:
        port = self._ports.get(port_id)
        if port is None:
            raise KeyError(f"unknown port {port_id}")
        port.session_id = session_id
