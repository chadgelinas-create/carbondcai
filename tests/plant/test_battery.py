"""Battery energy, coupling, quality and deterministic replay."""

from dataclasses import asdict, replace
from datetime import timedelta
from itertools import product
from pathlib import Path

import pytest

from skid2.application.simulation import SimulationService, SimulationState
from skid2.contracts import DataQuality, ManualClock
from skid2.plant.battery import BatteryConfig, battery_limits, battery_power
from skid2.plant.model import PlantModel, PlantState, PlantValue, ValueStatus
from skid2.plant.profiles import INITIAL_AT, plant_profile
from skid2.sim.battery import BatteryInputs
from skid2.sim.converter import ConverterInputs
from skid2.sim.dc600 import Dc600Inputs
from skid2.sim.faults import FaultRequest, FaultType


def config() -> BatteryConfig:
    return BatteryConfig("test", 100.0, 50.0, 0.8, 0.9, 100.0, 100.0)


def reading(values: tuple[PlantValue, ...], name: str) -> PlantValue:
    return next(item for item in values if item.name == name)


def number(state: SimulationState | PlantState, name: str) -> float:
    plant = state.plant if isinstance(state, SimulationState) else state
    value = reading(plant.bess, name).value
    assert value is not None
    return value


def service(*, solar: bool = False, seconds: float = 1) -> SimulationService:
    result = SimulationService(
        ManualClock(INITIAL_AT), timedelta(seconds=seconds), Path("protocol"), seed=0
    )
    result.set_profile("solar_cloud" if solar else "stage0", 0, battery_profile="battery_3500")
    return result


def request(
    simulator: SimulationService, power: float, *, limit: float = 100, ramp: float | None = None
) -> None:
    simulator.set_inputs(
        "cnvtr-1",
        ConverterInputs(
            enabled=True,
            requested_power=power,
            available_power=100,
            charge_power_limit=limit,
            discharge_power_limit=limit,
            test_ramp_per_step=ramp,
        ),
    )


def load(simulator: SimulationService, power: float) -> None:
    simulator.set_inputs("dc600", Dc600Inputs(demand_power=power, available_power=100))


@pytest.mark.parametrize(
    ("power", "energy", "loss"),
    [(60, 50 - 60 / 0.9 / 60, 60 / 0.9 - 60), (-60, 50 + 60 * 0.8 / 60, 12), (0, 50, 0)],
)
def test_directional_energy_and_loss(power: float, energy: float, loss: float) -> None:
    result = battery_power(config(), 50, power, 60)
    assert result.energy_kwh == pytest.approx(energy)
    assert result.loss_kw == pytest.approx(loss)


def test_energy_bounds_and_conservation_sweep() -> None:
    for energy, seconds, charge_efficiency, discharge_efficiency in product(
        (0.0, 0.0001, 50.0, 99.9999, 100.0), (0.001, 1.0, 60.0), (0.8, 1.0), (0.9, 1.0)
    ):
        settings = replace(
            config(), charge_efficiency=charge_efficiency, discharge_efficiency=discharge_efficiency
        )
        charge, discharge = battery_limits(settings, energy, seconds)
        for power in (-min(100, charge), 0, min(100, discharge)):
            result = battery_power(settings, energy, power, seconds)
            assert 0 <= result.energy_kwh <= 100
            assert result.loss_kw >= 0
            assert result.energy_kwh - energy == pytest.approx(
                (-power - result.loss_kw) * seconds / 3600, abs=1e-12
            )


@pytest.mark.parametrize(
    "field",
    [
        "capacity_kwh",
        "initial_energy_kwh",
        "charge_efficiency",
        "discharge_efficiency",
        "charge_limit_kw",
        "discharge_limit_kw",
    ],
)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), True])
def test_bad_settings_are_rejected(field: str, value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        values = asdict(config())
        values[field] = value
        BatteryConfig(**values)


@pytest.mark.parametrize(
    "values",
    [
        {"capacity_kwh": 0},
        {"initial_energy_kwh": -1},
        {"initial_energy_kwh": 101},
        {"charge_efficiency": 0},
        {"discharge_efficiency": 1.01},
        {"charge_limit_kw": -1},
        {"discharge_limit_kw": -1},
        {"profile_id": ""},
        {"revision": "old"},
    ],
)
def test_settings_ranges_are_checked(values: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        settings = asdict(config())
        settings.update(values)
        BatteryConfig(**settings)


@pytest.mark.parametrize(
    ("energy", "seconds"), [(-1, 1), (101, 1), (50, 0), (50, 61), (True, 1), (50, float("nan"))]
)
def test_bad_energy_or_time_is_rejected(energy: float, seconds: float) -> None:
    with pytest.raises(ValueError):
        battery_limits(config(), energy, seconds)


@pytest.mark.parametrize("power", [True, float("inf"), 101, -101])
def test_bad_power_is_rejected(power: float) -> None:
    with pytest.raises(ValueError):
        battery_power(config(), 50, power, 1)


def test_idle_discharge_charge_and_shared_ledger() -> None:
    simulator = service(solar=True)
    idle = simulator.step()
    assert idle.failure is None
    assert number(idle, "Battery energy") == 1750
    load(simulator, 100)
    request(simulator, 50)
    discharged = simulator.step()
    assert discharged.failure is None
    actual = number(discharged, "Actual power")
    assert actual > 0
    assert number(discharged, "Battery energy") == pytest.approx(1750 - actual / 0.95 / 3600)
    load(simulator, 0)
    request(simulator, -50)
    charged = simulator.step()
    assert charged.failure is None
    assert number(charged, "Actual power") == -50
    assert number(charged, "Energy change") == pytest.approx(50 * 0.95 / 3600)
    assert reading(charged.plant.ledger, "Battery loss").value == pytest.approx(2.5)
    assert number(charged, "Battery SOC") == number(charged, "Battery energy") / 3500
    assert all(value.value is None for value in charged.plant.ports[0].ledger)


def test_bms_limit_and_converter_limit_override_ramped_request() -> None:
    simulator = service()
    load(simulator, 100)
    request(simulator, 100)
    assert number(simulator.step(), "Actual power") == 100
    request(simulator, 100, limit=20, ramp=1)
    limited = simulator.step()
    assert limited.failure is None
    assert number(limited, "Accepted power") == 99
    assert number(limited, "Actual power") == 20
    simulator.set_inputs(
        "ba1-1", BatteryInputs(main_contactor=True, discharge_power_limit=5, charge_power_limit=100)
    )
    limited = simulator.step()
    assert number(limited, "Actual power") == 5
    assert reading(limited.plant.bess, "Actual power").reason == "Battery limit"
    assert number(limited, "Request not met") == 95


@pytest.mark.parametrize(
    "kind",
    [
        FaultType.COMMUNICATION,
        FaultType.TIMEOUT,
        FaultType.STUCK,
        FaultType.INVALID,
        FaultType.RANGE,
    ],
)
def test_bad_limits_pause_without_changing_reported_values(kind: FaultType) -> None:
    simulator = service()
    load(simulator, 100)
    request(simulator, 50)
    before = simulator.step()
    point = (
        "bess.cluster.limit.discharge.power"
        if kind in {FaultType.STUCK, FaultType.INVALID, FaultType.RANGE}
        else None
    )
    simulator.start_fault(
        FaultRequest(
            "ba1-1",
            kind,
            point_id=point,
            test_value=9999 if kind is FaultType.RANGE else None,
            duration_steps=1,
        )
    )
    bad = simulator.step()
    assert bad.failure is None
    assert bad.plant.balance is ValueStatus.MISSING
    assert number(bad, "Battery energy") == number(before, "Battery energy")
    assert reading(bad.plant.bess, "Battery energy").quality is DataQuality.STALE
    assert reading(bad.plant.bess, "Actual power").value is None
    telemetry = next(item for item in bad.devices if item.device_id == "ba1-1").values
    assert next(
        item.value for item in telemetry if item.logical_id == "bess.cluster.limit.discharge.power"
    ) == (9999 if kind is FaultType.RANGE else 100)
    recovered = simulator.step()
    assert recovered.failure is None
    assert number(recovered, "Energy change") == pytest.approx(-50 / 0.95 / 3600)


def test_bad_charge_limit_does_not_block_valid_discharge() -> None:
    simulator = service()
    request(simulator, 50)
    load(simulator, 100)
    simulator.step()
    simulator.start_fault(
        FaultRequest("ba1-1", FaultType.INVALID, point_id="bess.cluster.limit.charge.power")
    )
    state = simulator.step()
    assert state.failure is None
    assert number(state, "Actual power") == 50
    assert reading(state.plant.bess, "Charge limit").value is None


def test_zero_limit_and_inactive_device_are_known_zero() -> None:
    simulator = service()
    request(simulator, 50)
    simulator.set_inputs("ba1-1", BatteryInputs(main_contactor=True))
    state = simulator.step()
    assert state.failure is None
    assert number(state, "Actual power") == 0
    simulator.set_inputs(
        "ba1-1",
        BatteryInputs(main_contactor=False, charge_power_limit=100, discharge_power_limit=100),
    )
    state = simulator.step()
    assert state.failure is None
    assert number(state, "Actual power") == 0
    assert reading(state.plant.bess, "Actual power").reason == "Battery or converter is inactive"


def test_thermal_derate_is_applied_once() -> None:
    simulator = service()
    request(simulator, 100)
    load(simulator, 100)
    simulator.step()
    simulator.start_fault(FaultRequest("cnvtr-1", FaultType.DERATE))
    state = simulator.step()
    assert state.failure is None
    assert number(state, "Actual power") == 50
    assert reading(state.plant.bess, "Actual power").reason == "Temperature limit"


def test_charge_uses_only_power_served_by_bus() -> None:
    simulator = service(seconds=60)
    request(simulator, -100)
    state = simulator.step()
    assert state.failure is None
    assert -100 < number(state, "Actual power") < 0
    assert number(state, "Energy change") == pytest.approx(
        -number(state, "Actual power") * 0.95 / 60
    )
    assert number(state, "Request not met") == pytest.approx(100 + number(state, "Actual power"))


def test_restore_replay_and_invalid_selection_keep_state() -> None:
    simulator = service()
    request(simulator, 50)
    load(simulator, 100)
    saved = simulator.step()
    simulator.save_snapshot()
    expected = [simulator.step() for _ in range(3)]
    restored = simulator.restore()
    assert restored.plant == saved.plant
    assert restored.battery_history == saved.battery_history
    assert [simulator.step() for _ in range(3)] == expected
    assert simulator.replay().plant_trace_hash == expected[-1].plant_trace_hash
    before = simulator.state()
    with pytest.raises(ValueError, match="unknown battery"):
        simulator.set_profile("stage0", 0, battery_profile="not-real")
    assert simulator.state() == before
    reset = simulator.reset()
    assert number(reset, "Battery energy") == 1750
    assert reset.battery_history == ()


def test_energy_transition_cannot_be_forged() -> None:
    simulator = service()
    before = simulator.state().plant
    after = simulator.step().plant
    changed = replace(
        after,
        bess=tuple(
            replace(value, value=1751.0 if value.name == "Battery energy" else 1751 / 3500)
            if value.name in {"Battery energy", "Battery SOC"}
            else value
            for value in after.bess
        ),
    )
    with pytest.raises(ValueError, match="energy change"):
        PlantModel(after.configuration).validate(
            changed, changed.at, changed.sequence, previous=before
        )


def test_long_run_energy_stays_bounded_and_replays() -> None:
    simulator = service(seconds=60)
    request(simulator, 100)
    load(simulator, 100)
    first = simulator.step()
    assert first.failure is None
    runtime = simulator._runtime
    runtime.start()
    start = runtime.snapshot()
    total_loss = number(first, "Energy loss rate") / 60
    total_transfer = number(first, "Actual power") / 60
    for _ in range(1439):
        runtime.advance()
        state = runtime.snapshot().plant
        assert state is not None
        assert 0 <= number(state, "Battery SOC") <= 1
        total_loss += number(state, "Energy loss rate") / 60
        total_transfer += number(state, "Actual power") / 60
    assert state is not None
    assert number(state, "Battery energy") == 0
    assert total_loss + total_transfer == pytest.approx(1750, abs=1e-8)
    expected = runtime.snapshot().plant_trace
    runtime.stop()
    runtime.restore(start)
    runtime.start()
    for step in range(1, 1440):
        runtime.advance()
        assert runtime.snapshot().plant == expected[step].state
    assert len(runtime.snapshot().plant_trace) == 1440


def test_profile_selection_is_explicit() -> None:
    assert plant_profile("stage0", seed=0, step_seconds=1).battery is None
    for profile, capacity in (
        ("battery_2700", 2700),
        ("battery_3500", 3500),
        ("battery_3850", 3850),
    ):
        settings = plant_profile("stage0", seed=0, step_seconds=1, battery_profile=profile)
        assert settings.battery is not None
        assert settings.battery.capacity_kwh == capacity
        assert settings.battery.initial_energy_kwh == capacity / 2


@pytest.mark.parametrize(
    ("energy", "power"), [(0, 100), (0.001, 100), (3500, -100), (3499.999, -100)]
)
def test_coupled_empty_full_and_partial_steps(energy: float, power: float) -> None:
    simulator = service(solar=power < 0)
    request(simulator, power)
    load(simulator, 100 if power > 0 else 0)
    sample = simulator.step()
    inputs = tuple(
        (entry.device_id, entry.result) for entry in simulator._runtime.snapshot().trace[-7:]
    )
    settings = sample.plant.configuration
    assert settings.battery is not None
    settings = replace(settings, battery=replace(settings.battery, initial_energy_kwh=energy))
    model = PlantModel(settings)
    before = model.initial_state()
    result = model.step(
        state=before,
        inputs=inputs,
        configuration=settings,
        at=before.at + settings.step,
        dt=settings.step,
    )
    model.validate(result.state, result.state.at, 1, previous=before)
    assert 0 <= number(result.state, "Battery energy") <= 3500
    assert number(result.state, "Battery energy") == (0 if power > 0 else 3500)
    assert abs(number(result.state, "Actual power")) < 100
    assert number(result.state, "Request not met") > 0


def test_missing_bms_limit_blocks_only_calculated_transfer() -> None:
    simulator = service()
    request(simulator, 50)
    load(simulator, 100)
    sample = simulator.step()
    settings = sample.plant.configuration
    model = PlantModel(settings)
    inputs = []
    for entry in simulator._runtime.snapshot().trace[-7:]:
        device_result = entry.result
        if entry.device_id == "ba1-1" and device_result is not None:
            device_result = replace(
                device_result,
                telemetry=tuple(
                    value
                    for value in device_result.telemetry
                    if value.logical_id != "bess.cluster.limit.discharge.power"
                ),
            )
        inputs.append((entry.device_id, device_result))
    result = model.step(
        state=sample.plant,
        inputs=tuple(inputs),
        configuration=settings,
        at=sample.at + settings.step,
        dt=settings.step,
    )
    model.validate(result.state, result.state.at, 2, previous=sample.plant)
    assert result.state.balance is ValueStatus.MISSING
    assert number(result.state, "Battery energy") == number(sample, "Battery energy")


def test_retained_battery_trace_matches_charge_fault_and_recovery() -> None:
    import json

    expected = json.loads(Path("tests/fixtures/battery-trace.json").read_text())
    simulator = service(solar=True)
    request(simulator, -40)
    load(simulator, 0)
    for _ in range(3):
        simulator.step()
    frames = []
    for step in range(4):
        if step == 2:
            simulator.start_fault(FaultRequest("ba1-1", FaultType.COMMUNICATION, duration_steps=1))
        state = simulator.step()
        frames.append(
            {
                "sequence": state.sequence,
                "energy_kwh": number(state, "Battery energy"),
                "actual_kw": reading(state.plant.bess, "Actual power").value,
                "loss_kw": reading(state.plant.bess, "Energy loss rate").value,
                "quality": reading(state.plant.bess, "Battery energy").quality,
            }
        )
    assert frames == expected["frames"]
    assert state.plant_trace_hash == expected["plant_trace_hash"]
