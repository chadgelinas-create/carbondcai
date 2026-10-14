"""DC600 charger ↔ PMS handshake phase tracker (lab / sim)."""

from __future__ import annotations

from enum import StrEnum


class HandshakePhase(StrEnum):
    IDLE = "idle"
    REQUESTED = "requested"
    GRANTED = "granted"
    ACTIVE = "active"
    RELEASING = "releasing"
    FAULT = "fault"


class HandshakeMachine:
    """Tracks the high-level charger handshake phase for EMS status."""

    def __init__(self) -> None:
        self.phase: HandshakePhase = HandshakePhase.IDLE

    def reset(self) -> None:
        self.phase = HandshakePhase.IDLE
