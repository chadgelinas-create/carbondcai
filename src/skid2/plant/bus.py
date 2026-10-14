"""DC-link energy in kWh, power in kW, voltage in V and time in seconds."""

from dataclasses import dataclass
from math import isfinite, sqrt


@dataclass(frozen=True, slots=True)
class BusConfig:
    nominal_voltage_v: float
    max_voltage_v: float
    capacitance_f: float
    loss_ratio: float
    tolerance_kw: float

    def __post_init__(self) -> None:
        values = (
            self.nominal_voltage_v,
            self.max_voltage_v,
            self.capacitance_f,
            self.loss_ratio,
            self.tolerance_kw,
        )
        if any(isinstance(value, bool) or not isfinite(value) for value in values):
            raise ValueError("bus settings must be finite numbers")
        if not 0 < self.nominal_voltage_v <= self.max_voltage_v or self.capacitance_f <= 0:
            raise ValueError("bus voltage and capacitance must be positive")
        if not 0 <= self.loss_ratio < 1 or self.tolerance_kw <= 0:
            raise ValueError("bus loss and tolerance are invalid")
        if not isfinite(self.max_energy_kwh) or self.max_energy_kwh <= 0:
            raise ValueError("bus energy range is invalid")

    @property
    def initial_energy_kwh(self) -> float:
        return self.capacitance_f * self.nominal_voltage_v * self.nominal_voltage_v / 7_200_000.0

    @property
    def max_energy_kwh(self) -> float:
        return self.capacitance_f * self.max_voltage_v * self.max_voltage_v / 7_200_000.0


@dataclass(frozen=True, slots=True)
class BusResult:
    energy_kwh: float
    voltage_v: float
    net_kw: float
    supplied_kw: float
    served_kw: float
    loss_kw: float
    stored_kw: float
    unused_kw: float
    unmet_kw: float
    residual_kw: float


def step_bus(
    *,
    energy_kwh: float,
    supply_kw: float,
    demand_kw: float,
    seconds: float,
    config: BusConfig,
) -> BusResult:
    """Advance energy and account for power at both model bounds."""

    values = (energy_kwh, supply_kw, demand_kw, seconds)
    if any(isinstance(value, bool) or not isfinite(value) for value in values):
        raise ValueError("bus inputs must be finite numbers")
    if supply_kw < 0 or demand_kw < 0 or not 0.001 <= seconds <= 60:
        raise ValueError("bus power or step is outside the model range")
    if not 0 <= energy_kwh <= config.max_energy_kwh:
        raise ValueError("bus energy is outside the model range")

    hours = seconds / 3600.0
    incoming_kw = supply_kw * (1.0 - config.loss_ratio)
    net_kw = incoming_kw - demand_kw
    next_energy = min(max(energy_kwh + net_kw * hours, 0.0), config.max_energy_kwh)
    stored_kw = (next_energy - energy_kwh) / hours
    unused_kw = max(net_kw - stored_kw, 0.0) if next_energy == config.max_energy_kwh else 0.0
    unmet_kw = min(max(stored_kw - net_kw, 0.0), demand_kw) if next_energy == 0 else 0.0
    supplied_kw = min(max((incoming_kw - unused_kw) / (1.0 - config.loss_ratio), 0.0), supply_kw)
    loss_kw = supplied_kw * config.loss_ratio
    served_kw = demand_kw - unmet_kw
    residual_kw = supplied_kw - served_kw - loss_kw - stored_kw
    if not isfinite(residual_kw) or abs(residual_kw) > config.tolerance_kw:
        raise ValueError("bus balance exceeds the numerical tolerance")

    return BusResult(
        energy_kwh=next_energy,
        voltage_v=sqrt(7_200_000.0 * next_energy / config.capacitance_f),
        net_kw=net_kw,
        supplied_kw=supplied_kw,
        served_kw=served_kw,
        loss_kw=loss_kw,
        stored_kw=stored_kw,
        unused_kw=supply_kw - supplied_kw,
        unmet_kw=unmet_kw,
        residual_kw=residual_kw,
    )
