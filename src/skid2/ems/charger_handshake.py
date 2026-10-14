"""Logical DC600 demand/available handshake for the EMS charger (SIM).

Register map is Appendix A (40001 demand, 40101 Available Power). The charger
is master of ports: it posts aggregate demand, the plant answers with available
power, then the session may charge, limit, time out, or recover.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class HandshakePhase(StrEnum):
    IDLE = "idle"
    REQUESTED = "requested"
    ACKNOWLEDGED = "acknowledged"
    CHARGING = "charging"
    LIMITED = "limited"
    TIMEOUT = "timeout"
    RECOVERING = "recovering"
    OFFLINE = "offline"


_LIVE = frozenset(
    {
        HandshakePhase.REQUESTED,
        HandshakePhase.ACKNOWLEDGED,
        HandshakePhase.CHARGING,
        HandshakePhase.LIMITED,
        HandshakePhase.RECOVERING,
    }
)


@dataclass
class HandshakeMachine:
    """One-step-per-poll state machine. Ack takes one poll; timeout is sticky."""

    phase: HandshakePhase = HandshakePhase.IDLE

    def step(
        self,
        *,
        occupied: bool,
        demand_kw: float,
        available_kw: float,
        communication_ok: bool,
        allocated_kw: float = 0.0,
        fresh: bool = True,
    ) -> HandshakePhase:
        if not occupied or demand_kw <= 0:
            self.phase = HandshakePhase.IDLE
            return self.phase
        if not communication_ok:
            self.phase = HandshakePhase.OFFLINE
            return self.phase
        if self.phase is HandshakePhase.OFFLINE:
            self.phase = HandshakePhase.RECOVERING
            return self.phase
        if not fresh:
            self.phase = HandshakePhase.TIMEOUT
            return self.phase
        limited = allocated_kw + 0.5 < demand_kw or available_kw + 0.5 < demand_kw
        if self.phase is HandshakePhase.TIMEOUT:
            self.phase = HandshakePhase.RECOVERING
            return self.phase
        if self.phase is HandshakePhase.IDLE:
            self.phase = HandshakePhase.REQUESTED
            return self.phase
        if self.phase is HandshakePhase.REQUESTED:
            self.phase = HandshakePhase.ACKNOWLEDGED
            return self.phase
        if self.phase in {HandshakePhase.ACKNOWLEDGED, HandshakePhase.RECOVERING}:
            self.phase = HandshakePhase.LIMITED if limited else HandshakePhase.CHARGING
            return self.phase
        if self.phase in _LIVE:
            self.phase = HandshakePhase.LIMITED if limited else HandshakePhase.CHARGING
            return self.phase
        self.phase = HandshakePhase.REQUESTED
        return self.phase
