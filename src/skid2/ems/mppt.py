"""CNVTR-2 MPPT charge-control setpoints owned by the EMS (SIM write-back)."""

from __future__ import annotations

from dataclasses import dataclass

from skid2.ems.config import ConfigService


@dataclass(frozen=True, slots=True)
class MpptDecision:
    applying: bool
    enabled: bool
    requested_kw: float
    reason: str


class MpptController:
    """Latch PV off at mppt_off until SOC falls to mppt_restart."""

    def __init__(self) -> None:
        self._latched_off = False

    def evaluate(self, soc: float, available_kw: float, config: ConfigService) -> MpptDecision:
        hold = config.numeric("solar", "mppt_hold")
        resume = config.numeric("solar", "mppt_resume")
        taper = config.numeric("solar", "mppt_taper")
        off = config.numeric("solar", "mppt_off")
        restart = config.numeric("solar", "mppt_restart")
        limit = max(0.0, available_kw)

        if soc >= off:
            self._latched_off = True
            return MpptDecision(False, False, 0.0, "MPPT off — battery at charge ceiling")
        if self._latched_off and soc > restart:
            return MpptDecision(False, False, 0.0, "MPPT latched off until restart SOC")
        self._latched_off = False
        if soc >= hold:
            return MpptDecision(False, True, 0.0, "MPPT hold")
        if soc >= resume:
            hold_power = limit * 0.25
            return MpptDecision(False, True, hold_power, "MPPT resume band — reduced")
        if soc >= taper:
            return MpptDecision(True, True, limit * 0.5, "MPPT taper")
        return MpptDecision(True, True, limit, "MPPT tracking")
