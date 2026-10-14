"""Converter ramp, losses and temperature derating (Week 2 SIM)."""

from datetime import UTC, datetime, timedelta

from skid2.sim.converter import (
    CONVERTER_EFFICIENCY,
    ConverterInputs,
    ConverterModel,
    ConverterRole,
    ConverterState,
)
from skid2.sim.device import (
    DeviceIdentity,
    DeviceLifecycle,
    DeviceStep,
    DeviceStepResult,
    EvidenceClassification,
)

NOW = datetime(2026, 10, 13, 12, tzinfo=UTC)


def _model() -> ConverterModel:
    return ConverterModel(
        DeviceIdentity("cnvtr-1", "CNVTR-1"),
        (),
        ConverterRole.BATTERY,
    )


def _step(
    state: ConverterState, inputs: ConverterInputs, *, seconds: float = 1
) -> DeviceStepResult[ConverterState]:
    return _model().step(
        DeviceStep(state, inputs, (), None, NOW, timedelta(seconds=seconds)),
    )


def test_room_temperature_does_not_derate() -> None:
    model = _model()
    result = _step(
        model.initial_state(None),
        ConverterInputs(
            enabled=True,
            requested_power=200,
            available_power=250,
            charge_power_limit=200,
            discharge_power_limit=250,
            temperature=25,
        ),
    )
    assert result.state.accepted_power == 200
    by_id = {item.logical_id: item.value for item in result.telemetry}
    assert by_id["cnvtr1.thermal.derated"] is False
    assert by_id["cnvtr1.loss.power"] == 200 * (1 - CONVERTER_EFFICIENCY)
    assert by_id["cnvtr1.efficiency"] == CONVERTER_EFFICIENCY


def test_high_temperature_derates_available_power() -> None:
    model = _model()
    result = _step(
        model.initial_state(None),
        ConverterInputs(
            enabled=True,
            requested_power=200,
            available_power=200,
            charge_power_limit=200,
            discharge_power_limit=200,
            temperature=70,
        ),
    )
    assert result.state.accepted_power == 100
    assert any(fault.fault_id.endswith("temperature_derate") for fault in result.faults)


def test_cutoff_temperature_zeros_power() -> None:
    model = _model()
    result = _step(
        model.initial_state(None),
        ConverterInputs(
            enabled=True,
            requested_power=200,
            available_power=200,
            charge_power_limit=200,
            discharge_power_limit=200,
            temperature=80,
        ),
    )
    assert result.state.accepted_power == 0


def test_default_ramp_is_visible_on_short_steps() -> None:
    model = _model()
    first = _step(
        model.initial_state(None),
        ConverterInputs(
            enabled=True,
            requested_power=200,
            available_power=250,
            charge_power_limit=200,
            discharge_power_limit=250,
        ),
        seconds=0.1,
    )
    assert 0 < first.state.accepted_power < 200
    second = _step(
        ConverterState(DeviceLifecycle.ACTIVE, first.state.accepted_power),
        ConverterInputs(
            enabled=True,
            requested_power=200,
            available_power=250,
            charge_power_limit=200,
            discharge_power_limit=250,
        ),
        seconds=1,
    )
    assert second.state.accepted_power == 200


def test_command_outcome_accepted() -> None:
    from skid2.sim.device import CommandDisposition, DeviceCommand

    model = _model()
    command = DeviceCommand("cmd-1", "cnvtr1.power", NOW)
    result = model.step(
        DeviceStep(
            model.initial_state(None),
            ConverterInputs(
                enabled=True, requested_power=40, available_power=100, discharge_power_limit=100
            ),
            (command,),
            None,
            NOW,
            timedelta(seconds=1),
        )
    )
    assert result.command_results[0].disposition is CommandDisposition.ACCEPTED
    assert result.command_results[0].evidence_classification is EvidenceClassification.PROVISIONAL
