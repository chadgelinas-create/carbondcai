"""Battery energy in kWh, bus power in kW and elapsed time in seconds."""

from dataclasses import dataclass
from math import isfinite, ulp


@dataclass(frozen=True, slots=True)
class BatteryConfig:
    profile_id: str
    capacity_kwh: float
    initial_energy_kwh: float
    charge_efficiency: float
    discharge_efficiency: float
    charge_limit_kw: float
    discharge_limit_kw: float
    revision: str = "battery-1"

    def __post_init__(self) -> None:
        values = (
            self.capacity_kwh,
            self.initial_energy_kwh,
            self.charge_efficiency,
            self.discharge_efficiency,
            self.charge_limit_kw,
            self.discharge_limit_kw,
        )
        if any(isinstance(value, bool) or not isfinite(value) for value in values):
            raise ValueError("battery settings must be finite numbers")
        if not self.profile_id or self.revision != "battery-1":
            raise ValueError("battery profile metadata is invalid")
        if self.capacity_kwh <= 0 or not 0 <= self.initial_energy_kwh <= self.capacity_kwh:
            raise ValueError("battery energy is outside its capacity")
        if not 0 < self.charge_efficiency <= 1 or not 0 < self.discharge_efficiency <= 1:
            raise ValueError("battery efficiency must be greater than zero and at most one")
        if min(self.charge_limit_kw, self.discharge_limit_kw) < 0:
            raise ValueError("battery limits must be non-negative")


@dataclass(frozen=True, slots=True)
class BatteryPower:
    energy_kwh: float
    loss_kw: float


def battery_limits(config: BatteryConfig, energy_kwh: float, seconds: float) -> tuple[float, float]:
    """Return the charge and discharge bounds at the bus."""

    if any(isinstance(value, bool) or not isfinite(value) for value in (energy_kwh, seconds)):
        raise ValueError("battery energy and time must be finite numbers")
    if not 0 <= energy_kwh <= config.capacity_kwh or not 0.001 <= seconds <= 60:
        raise ValueError("battery energy or step is outside the model range")
    hours = seconds / 3600.0
    charge = (config.capacity_kwh - energy_kwh) / (config.charge_efficiency * hours)
    discharge = energy_kwh * config.discharge_efficiency / hours
    if not isfinite(charge) or not isfinite(discharge):
        raise ValueError("battery energy bounds are not finite")
    return charge, discharge


def battery_power(
    config: BatteryConfig, energy_kwh: float, actual_kw: float, seconds: float
) -> BatteryPower:
    """Update storage from actual bus transfer, after allocation."""

    charge, discharge = battery_limits(config, energy_kwh, seconds)
    if isinstance(actual_kw, bool) or not isfinite(actual_kw):
        raise ValueError("battery power must be a finite number")
    if (
        not -min(charge, config.charge_limit_kw)
        <= actual_kw
        <= min(discharge, config.discharge_limit_kw)
    ):
        raise ValueError("battery power exceeds its energy or static limit")
    stored_kw = (
        -actual_kw / config.discharge_efficiency
        if actual_kw > 0
        else -actual_kw * config.charge_efficiency
    )
    energy = energy_kwh + stored_kw * seconds / 3600.0
    tolerance = 4 * ulp(config.capacity_kwh)
    if not -tolerance <= energy <= config.capacity_kwh + tolerance:
        raise ValueError("battery transfer exceeds stored energy bounds")
    # Only remove round-off at a physical boundary.
    energy = min(max(energy, 0.0), config.capacity_kwh)
    return BatteryPower(energy, max(-actual_kw - stored_kw, 0.0))
