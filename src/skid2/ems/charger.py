"""Charger / SOC availability control point for the DC600 group."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from skid2.ems.config import ConfigService


class ChargerMode(StrEnum):
    NORMAL = "normal"
    LIMITED = "limited"
    MAINTENANCE = "maintenance"
    TERMINATED = "terminated"
    RECOVERING = "recovering"


@dataclass(frozen=True, slots=True)
class ChargerDecision:
    mode: ChargerMode
    allow_new_sessions: bool
    energy_cap_kwh: float | None
    terminate_active: bool
    driver_message: str
    soc: float


class ChargerAvailability:
    """Gate EV sessions from BESS SOC thresholds owned by EMS config."""

    def __init__(self, config: ConfigService) -> None:
        self._config = config
        self._latched_terminate = False

    def evaluate(self, soc: float) -> ChargerDecision:
        limited = self._config.numeric("charger", "soc_limited")
        maintenance = self._config.numeric("charger", "soc_maintenance")
        terminate = self._config.numeric("charger", "soc_terminate")
        recover = self._config.numeric("charger", "soc_recover")
        cap = self._config.numeric("charger", "session_cap_kwh")
        station = self._config.text("charger", "station_message")

        if self._latched_terminate and soc < recover:
            return ChargerDecision(
                ChargerMode.TERMINATED,
                False,
                None,
                True,
                "Charging unavailable — battery critically low",
                soc,
            )
        if self._latched_terminate and soc >= recover:
            self._latched_terminate = False
            return ChargerDecision(
                ChargerMode.RECOVERING,
                True,
                None,
                False,
                station,
                soc,
            )
        if soc <= terminate:
            self._latched_terminate = True
            return ChargerDecision(
                ChargerMode.TERMINATED,
                False,
                None,
                True,
                "Charging terminated — battery critically low",
                soc,
            )
        if soc <= maintenance:
            return ChargerDecision(
                ChargerMode.MAINTENANCE,
                False,
                cap,
                False,
                "New sessions unavailable — battery SOC below threshold",
                soc,
            )
        if soc <= limited:
            return ChargerDecision(
                ChargerMode.LIMITED,
                True,
                cap,
                False,
                f"Limited charging — sessions capped at {cap:g} kWh",
                soc,
            )
        return ChargerDecision(ChargerMode.NORMAL, True, None, False, station, soc)
