"""Stabiliti operational state and AC/DC SIM telemetry (Week 5)."""

from datetime import UTC, datetime, timedelta

from skid2.sim.device import DeviceIdentity, DeviceLifecycle, DeviceStep, DeviceStepResult
from skid2.sim.stabiliti import StabilitiInputs, StabilitiModel, StabilitiState

NOW = datetime(2026, 10, 13, 16, tzinfo=UTC)


def _model() -> StabilitiModel:
    return StabilitiModel(DeviceIdentity("st-4", "ST-4"), ())


def _step(state: StabilitiState, inputs: StabilitiInputs) -> DeviceStepResult[StabilitiState]:
    return _model().step(DeviceStep(state, inputs, (), None, NOW, timedelta(seconds=1)))


def _value(result: DeviceStepResult[StabilitiState], logical_id: str) -> object:
    return next(item.value for item in result.telemetry if item.logical_id == logical_id)


def test_bus_load_becomes_ac_and_dc_power() -> None:
    result = _step(
        _model().initial_state(None),
        StabilitiInputs(enabled=True, available_power=60, power_limit=60, bus_load=45),
    )
    assert _value(result, "st-4.ac.port.power") == 45
    assert _value(result, "st-4.dc.in.power") == 45
    assert _value(result, "st-4.ac.port.volts") == 480
    assert _value(result, "st-4.operational.state") == 2


def test_offline_then_recovering() -> None:
    model = _model()
    lost = _step(
        model.initial_state(None),
        StabilitiInputs(communication_ok=False, available_power=40, power_limit=40, bus_load=10),
    )
    assert lost.state.lifecycle is DeviceLifecycle.OFFLINE
    assert _value(lost, "st-4.operational.state") == 0
    recovered = _step(
        lost.state,
        StabilitiInputs(communication_ok=True, available_power=40, power_limit=40, bus_load=10),
    )
    assert _value(recovered, "st-4.operational.state") == 5


def test_thermal_derate_reduces_ac() -> None:
    result = _step(
        _model().initial_state(None),
        StabilitiInputs(
            enabled=True,
            available_power=60,
            power_limit=60,
            bus_load=60,
            maximum_temperature=70,
        ),
    )
    assert result.state.derated is True
    assert _value(result, "st-4.ac.port.power") == 30
    assert any(fault.fault_id.endswith("temperature_derate") for fault in result.faults)
