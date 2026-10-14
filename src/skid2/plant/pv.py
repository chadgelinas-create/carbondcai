"""Deterministic solar resources and conversion accounting."""

from dataclasses import dataclass
from math import isfinite

PV_IDS = ("cnvtr-2",)
PROFILE_IDS = (
    "stage0",
    "solar_step",
    "solar_ramp",
    "solar_daylight",
    "solar_cloud",
    "solar_variable",
)


@dataclass(frozen=True, slots=True)
class PvConfig:
    device_id: str
    destination: str
    efficiency: float
    limit_kw: float
    points: tuple[tuple[float, float], ...]
    interpolation: str
    interval_seconds: float
    revision: str = "solar-2"
    unit: str = "kW"
    end: str = "hold"

    def __post_init__(self) -> None:
        if self.device_id not in PV_IDS or self.destination != "dc":
            raise ValueError("solar device or destination is invalid")
        pv_limit(
            available_kw=0,
            accepted_kw=self.limit_kw,
            limit_kw=self.limit_kw,
            efficiency=self.efficiency,
        )
        if (
            self.interpolation not in {"hold", "linear"}
            or self.unit != "kW"
            or self.end != "hold"
            or self.revision != "solar-2"
            or isinstance(self.interval_seconds, bool)
            or not isfinite(self.interval_seconds)
            or self.interval_seconds <= 0
        ):
            raise ValueError("solar profile metadata is invalid")
        if not isinstance(self.points, tuple) or not self.points or self.points[0][0] != 0:
            raise ValueError("solar profile must start at zero seconds")
        previous = -1.0
        for seconds, power in self.points:
            if any(isinstance(value, bool) or not isfinite(value) for value in (seconds, power)):
                raise ValueError("solar samples must be finite numbers")
            if seconds <= previous or not 0 <= power <= 6553.5:
                raise ValueError("solar samples must be ordered and within the test range")
            previous = seconds


@dataclass(frozen=True, slots=True)
class PvPower:
    actual_kw: float
    loss_kw: float
    curtailed_kw: float


def pv_available(config: PvConfig, seconds: float) -> float:
    """Sample elapsed simulation time; hold the last value after the profile."""

    if isinstance(seconds, bool) or not isfinite(seconds) or seconds < 0:
        raise ValueError("solar time must be a finite non-negative number")
    for (start, before), (end, after) in zip(config.points, config.points[1:], strict=False):
        if seconds < end:
            if config.interpolation == "hold":
                return before
            return before + (after - before) * (seconds - start) / (end - start)
    return config.points[-1][1]


def pv_limit(
    *, available_kw: float, accepted_kw: float, limit_kw: float, efficiency: float
) -> float:
    """Return the maximum power at the converter output."""

    values = (available_kw, accepted_kw, limit_kw, efficiency)
    if any(isinstance(value, bool) or not isfinite(value) for value in values):
        raise ValueError("solar inputs must be finite numbers")
    if min(available_kw, accepted_kw, limit_kw) < 0 or not 0 < efficiency <= 1:
        raise ValueError("solar power or efficiency is outside its range")
    return min(available_kw * efficiency, accepted_kw, limit_kw)


def pv_power(*, available_kw: float, actual_kw: float, efficiency: float) -> PvPower:
    """Account for solar power after the destination accepts it."""

    maximum = pv_limit(
        available_kw=available_kw, accepted_kw=actual_kw, limit_kw=actual_kw, efficiency=efficiency
    )
    if actual_kw > maximum:
        raise ValueError("solar output exceeds the available resource")
    # Keep division round-off within the resource bound.
    used = min(available_kw, actual_kw / efficiency)
    return PvPower(actual_kw, used - actual_kw, available_kw - used)
