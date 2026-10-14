"""EMS point groups and default configuration catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Role = Literal["viewer", "operator", "admin"]

DEVICE_GROUPS = ("bms", "cnvtr", "solar", "charger", "stabiliti", "supervisor")

DEVICE_IDS = ("ba1-1", "cnvtr-1", "cnvtr-2", "dc600", "st-4", "st-5", "st-6")

GROUP_DEVICES: dict[str, tuple[str, ...]] = {
    "bms": ("ba1-1",),
    "cnvtr": ("cnvtr-1", "cnvtr-2"),
    "solar": ("cnvtr-2",),
    "charger": ("dc600",),
    "stabiliti": ("st-4", "st-5", "st-6"),
    "supervisor": DEVICE_IDS,
}


@dataclass(frozen=True, slots=True)
class ConfigSpec:
    """One validated configuration key owned by the EMS app."""

    group_id: str
    key: str
    default: float | str
    unit: str
    minimum: float | None = None
    maximum: float | None = None
    text: bool = False


CONFIG_CATALOG: tuple[ConfigSpec, ...] = (
    ConfigSpec("bms", "pack_max_voltage", 730.0, "V", 600.0, 900.0),
    ConfigSpec("bms", "pack_min_voltage", 550.0, "V", 400.0, 700.0),
    ConfigSpec("bms", "charge_power_ceiling", 250.0, "kW", 0.0, 500.0),
    ConfigSpec("bms", "soc_hold", 0.95, "ratio", 0.5, 1.0),
    ConfigSpec("bms", "soc_resume", 0.90, "ratio", 0.4, 1.0),
    ConfigSpec("bms", "soc_taper", 0.85, "ratio", 0.3, 1.0),
    ConfigSpec("bms", "over_temperature", 55.0, "degC", 30.0, 80.0),
    ConfigSpec("solar", "mppt_hold", 0.95, "ratio", 0.5, 1.0),
    ConfigSpec("solar", "mppt_resume", 0.90, "ratio", 0.4, 1.0),
    ConfigSpec("solar", "mppt_taper", 0.85, "ratio", 0.3, 1.0),
    ConfigSpec("solar", "mppt_off", 0.98, "ratio", 0.5, 1.0),
    ConfigSpec("solar", "mppt_restart", 0.80, "ratio", 0.2, 1.0),
    ConfigSpec("solar", "mppt_weekly_full", 1.0, "ratio", 0.9, 1.0),
    ConfigSpec("cnvtr", "cnvtr1_power_limit", 250.0, "kW", 0.0, 500.0),
    ConfigSpec("cnvtr", "cnvtr2_power_limit", 300.0, "kW", 0.0, 500.0),
    ConfigSpec("charger", "soc_limited", 0.15, "ratio", 0.05, 0.5),
    ConfigSpec("charger", "soc_maintenance", 0.15, "ratio", 0.05, 0.4),
    ConfigSpec("charger", "soc_terminate", 0.10, "ratio", 0.02, 0.3),
    ConfigSpec("charger", "soc_recover", 0.22, "ratio", 0.1, 0.6),
    ConfigSpec("charger", "session_cap_kwh", 10.0, "kWh", 1.0, 100.0),
    ConfigSpec("charger", "station_message", "Welcome — charging available", "", text=True),
    ConfigSpec("stabiliti", "st4_power_limit", 60.0, "kW", 0.0, 200.0),
    ConfigSpec("stabiliti", "st5_power_limit", 60.0, "kW", 0.0, 200.0),
    ConfigSpec("stabiliti", "st6_power_limit", 60.0, "kW", 0.0, 200.0),
    ConfigSpec("stabiliti", "site_ac_load", 0.0, "kW", 0.0, 180.0),
    ConfigSpec("supervisor", "bus_voltage_setpoint", 750.0, "V", 700.0, 800.0),
    ConfigSpec("supervisor", "available_power_reserve", 20.0, "kW", 0.0, 200.0),
    ConfigSpec("supervisor", "bus_power_cap", 600.0, "kW", 0.0, 600.0),
    ConfigSpec("supervisor", "available_ramp_kw_s", 20.0, "kW/s", 0.0, 100.0),
    ConfigSpec("supervisor", "fault_recovery_s", 30.0, "s", 10.0, 120.0),
    ConfigSpec("supervisor", "bess_reserve_soc", 0.075, "ratio", 0.02, 0.2),
    ConfigSpec("supervisor", "bess_normal_soc", 0.186, "ratio", 0.1, 0.5),
    ConfigSpec("supervisor", "bess_reserve_floor", 0.043, "ratio", 0.02, 0.1),
)


def config_spec(group_id: str, key: str) -> ConfigSpec:
    for item in CONFIG_CATALOG:
        if item.group_id == group_id and item.key == key:
            return item
    raise KeyError(f"unknown config {group_id}.{key}")


def group_config_specs(group_id: str) -> tuple[ConfigSpec, ...]:
    return tuple(item for item in CONFIG_CATALOG if item.group_id == group_id)
