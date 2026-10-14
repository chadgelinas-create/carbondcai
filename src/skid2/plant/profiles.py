"""Repository-owned Stage 0 assumptions."""

from datetime import UTC, datetime
from hashlib import sha256

from skid2.plant.battery import BatteryConfig
from skid2.plant.bus import BusConfig
from skid2.plant.model import PlantConfig
from skid2.plant.pv import PROFILE_IDS, PV_IDS, PvConfig
from skid2.sim.device import (
    CapabilityKind,
    DeviceCapability,
    EvidenceClassification,
    ExecutionProfile,
    FidelityLevel,
)

INITIAL_AT = datetime(2026, 8, 1, 10, tzinfo=UTC)


BATTERY_CAPACITIES = {"battery_2700": 2700.0, "battery_3500": 3500.0, "battery_3850": 3850.0}


def plant_profile(
    profile_id: str,
    *,
    seed: int,
    step_seconds: float,
    battery_profile: str | None = None,
    battery: BatteryConfig | None = None,
    bus: BusConfig | None = None,
) -> PlantConfig:
    if profile_id not in PROFILE_IDS:
        raise ValueError("unknown plant profile")
    if battery is not None:
        if battery_profile != "custom" or battery.profile_id != "custom":
            raise ValueError("manual battery settings require the custom setup")
    elif battery_profile is not None:
        if battery_profile not in BATTERY_CAPACITIES:
            raise ValueError("unknown battery setup or missing custom settings")
        capacity = BATTERY_CAPACITIES[battery_profile]
        battery = BatteryConfig(battery_profile, capacity, capacity / 2, 0.95, 0.95, 100.0, 100.0)
    solar = profile_id != "stage0"
    active = {"runtime", "bus", "balance"} | ({"pv"} if solar else set())
    if battery is not None:
        active.add("bess")
    if bus is None:
        bus = BusConfig(750.0, 1500.0, 1.0, 0.0, 0.000001)
    return PlantConfig(
        battery=battery,
        profile_id=profile_id,
        revision="solar-2" if solar else "stage0-3",
        pv=solar_profile(profile_id, seed) if solar else (),
        seed=seed,
        step_seconds=step_seconds,
        initial_at=INITIAL_AT,
        bus=bus,
        capabilities=tuple(
            DeviceCapability(
                capability_id=f"plant.{name}",
                device_id="plant",
                kind=CapabilityKind.BEHAVIOR,
                evidence_classification=EvidenceClassification.PROVISIONAL,
                fidelity_level=(
                    FidelityLevel.LEVEL_2
                    if name == "bus"
                    else FidelityLevel.LEVEL_1
                    if name == "balance"
                    or (name == "pv" and solar)
                    or (name == "bess" and battery is not None)
                    else FidelityLevel.CONTRACT
                ),
                review_status="pending",
                available=name in active,
                enabled_profiles=(
                    frozenset({ExecutionProfile.STAGE_0}) if name in active else frozenset()
                ),
                decision_ids=("OD-007", "OD-009") if name == "bess" else (),
                source_ids=("SRC-005", "SRC-007", "SRC-008", "SRC-011", "SRC-016", "SRC-033")
                if name == "bess" and battery is not None
                else ("SRC-002", "SRC-006", "SRC-033", "SRC-036")
                if name in {"bus", "balance"}
                else ("SRC-002", "SRC-006", "SRC-036")
                if name == "pv"
                else ("SRC-002", "SRC-006"),
                assumptions=(
                    "Software simulation only; not checked against physical equipment",
                    (
                        "All devices share the same clock and saved state"
                        if name == "runtime"
                        else f"A {bus.capacitance_f:g} F energy buffer starts at {bus.nominal_voltage_v:g} V; 0-{bus.max_voltage_v:g} V are mathematical bounds, not equipment limits"
                        if name == "bus"
                        else f"Ideal converter paths and {bus.loss_ratio * 100:g}% bus loss; shared limits serve the same fraction of each load"
                        if name == "balance" and bus.loss_ratio != 0
                        else "Ideal converter paths and zero bus loss; shared limits serve the same fraction of each load"
                        if name == "balance"
                        else "Saved sunlight curves and 95% solar conversion efficiency; all solar enters the DC bus through CNVTR-2"
                        if name == "pv" and solar
                        else "Capacity, starting energy, directional efficiency and power limits are manual test settings; all entered power limits refer to the bus"
                        if name == "bess" and battery_profile == "custom"
                        else "Selected usable capacity, half-full start, 95% directional efficiency and 100 kW limits are software assumptions; all entered power limits refer to the bus"
                        if name == "bess" and battery is not None
                        else "This physical calculation is not available yet"
                    ),
                ),
            )
            for name in ("runtime", "bus", "balance", "pv", "bess", "load", "converter", "thermal")
        ),
        limitations=(
            (
                "Battery energy and limits are calculated; charging sessions, converter timing and temperature dynamics remain incomplete"
                if battery is not None
                else "Solar availability, conversion and unused power are calculated; battery, charging, converter timing and temperature models remain incomplete"
                if solar
                else "Bus energy and power balance are calculated; solar, battery, charging, converter and temperature models remain incomplete"
            ),
            "Device readings remain entered test values; calculated power is shown separately",
            (
                "750 V is a candidate nominal value; warning, action and trip settings are not approved"
                if bus.nominal_voltage_v == 750
                else f"{bus.nominal_voltage_v:g} V is a selected starting voltage; warning, action and trip settings are not approved"
            ),
            "Missing power readings pause the bus calculation; skipped intervals are counted and are not reconstructed on recovery",
            "Battery transfer is counted once at the battery converter; the solar converter contributes solar power only",
            "Stabiliti units draw from the DC bus only; calculated AC output equals actual bus power used, with ideal conversion",
            "Shared limits reduce all included sources or loads by the same fraction; this is a simulation assumption, not dispatch policy",
            "The solar converter is the current bus-former; authority selection, voltage regulation and fallback are not modeled",
            "The step range of 0.001 to 60 seconds is a software setting; equipment timing is unverified",
            (
                "Variable sunlight uses SHA-256 sample version 1 with separate device seeds; saved samples are authoritative"
                if solar
                else "The repeat number is saved with the run; it does not change the current calculations"
            ),
            *(SOLAR_ASSUMPTIONS if solar else ()),
            *(
                (
                    "Battery capacity is explicitly assumed, not installed or approved; 2.7/3.5/3.85 MWh evidence remains unresolved",
                    "Battery energy changes only with calculated bus transfer; idle self-consumption and self-discharge are not modeled",
                    "Battery efficiency covers the storage-to-bus path once; no separate battery converter loss is added",
                    "Battery limits are bus-facing test inputs; vendor limit reference points and thermal thresholds are not approved",
                    "Reported battery level remains entered telemetry; calculated battery level is derived only from stored energy",
                )
                if battery is not None
                else ()
            ),
            "This simulation does not verify electrical transients, protection, power quality or equipment safety",
        ),
        review_owner="Software lead",
        reviewers=("Controls", "QA"),
    )


SOLAR_ASSUMPTIONS = (
    "Solar inputs are kW at the resource; requests and limits are kW at converter output; solar loss is upstream of the bus and counted once",
    "Sunlight is sampled at each frame end and held for that step; the last profile value is held after its final sample",
    "Solar ratings and efficiency are provisional test settings, not installed equipment data",
    "All solar enters the DC bus through CNVTR-2; Stabiliti units have no separate solar input or export path",
    "Bad device data keeps physical output unavailable; communication loss does not imply a known physical zero",
)


def solar_profile(profile_id: str, seed: int) -> tuple[PvConfig, ...]:
    curves = {
        "solar_step": ((0, 0), (5, 1), (15, 0.3), (25, 1)),
        "solar_ramp": ((0, 0), (10, 1), (20, 0.2), (30, 1)),
        "solar_daylight": (
            (0, 0),
            (21600, 0),
            (32400, 0.7),
            (43200, 1),
            (54000, 0.7),
            (64800, 0),
            (86400, 0),
        ),
        "solar_cloud": ((0, 1), (5, 1), (10, 0.2), (15, 0.2), (20, 1), (30, 1)),
    }
    paths = []
    for device_id in PV_IDS:
        peak = 100.0
        if profile_id == "solar_variable":
            points = tuple(
                (
                    float(index * 5),
                    peak
                    * (
                        0.2
                        + 0.8
                        * int.from_bytes(
                            sha256(f"solar-1:{seed}:{device_id}:{index}".encode()).digest()[:8],
                            "big",
                        )
                        / 2**64
                    ),
                )
                for index in range(13)
            )
        else:
            points = tuple(
                (float(seconds), peak * fraction) for seconds, fraction in curves[profile_id]
            )
        paths.append(
            PvConfig(
                device_id=device_id,
                destination="dc",
                efficiency=0.95,
                limit_kw=100.0,
                points=points,
                interpolation="hold" if profile_id == "solar_step" else "linear",
                interval_seconds=10800.0 if profile_id == "solar_daylight" else 5.0,
            )
        )
    return tuple(paths)
