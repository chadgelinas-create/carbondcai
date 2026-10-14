"""MPPT hold / apply helper used when EMS config writes hit solar/cnvtr."""

from __future__ import annotations


class MpptController:
    """Tracks last applied MPPT-related setpoints for sim feedback."""

    def __init__(self) -> None:
        self.hold: bool = False
        self.last_applied: list[str] = []

    def note(self, message: str) -> None:
        self.last_applied.append(message)
        self.last_applied = self.last_applied[-20:]
