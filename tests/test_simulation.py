"""Simulator service scheduling, recovery, report, and replay tests."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from skid2.application.simulation import DeviceSummary, SimulationService, SimulationState
from skid2.contracts import ComponentStatus, DataQuality, ManualClock
from skid2.sim.battery import BatteryInputs
from skid2.sim.faults import FaultRequest, FaultTestState, FaultType, fault_state

NOW = datetime(2026, 8, 1, 10, tzinfo=UTC)


def service() -> SimulationService:
    return SimulationService(ManualClock(NOW), timedelta(seconds=1), Path("protocol"), seed=0)


def device(state: SimulationState, device_id: str) -> DeviceSummary:
    return next(item for item in state.devices if item.device_id == device_id)


def test_no_fault_keeps_the_existing_deterministic_trace() -> None:
    first = service()
    second = service()

    first_state = first.step()
    second_state = second.step()

    assert first_state.trace_hash == second_state.trace_hash
    assert first_state.devices == second_state.devices
    assert first_state.fault_tests == ()


def test_scheduled_timed_fault_activates_and_recovers_on_exact_steps() -> None:
    simulator = service()
    simulator.step()
    scheduled = simulator.start_fault(
        FaultRequest(
            "ba1-1",
            FaultType.COMMUNICATION,
            delay_steps=1,
            duration_steps=1,
        )
    )
    test = scheduled.fault_tests[0]
    assert test.test_id == "test-0001"
    assert test.start_step == 3
    assert fault_state(test, scheduled.sequence) is FaultTestState.SCHEDULED

    waiting = simulator.step()
    assert fault_state(test, waiting.sequence) is FaultTestState.SCHEDULED
    assert device(waiting, "ba1-1").health is ComponentStatus.HEALTHY

    active = simulator.step()
    battery = device(active, "ba1-1")
    assert battery.health is ComponentStatus.UNAVAILABLE
    assert battery.test_id == "test-0001"
    assert all(value.test_id == "test-0001" for value in battery.values)
    assert all(value.quality is DataQuality.COMM_ERROR for value in battery.values)

    recovered = simulator.step()
    battery = device(recovered, "ba1-1")
    assert fault_state(test, recovered.sequence) is FaultTestState.COMPLETE
    assert battery.health is ComponentStatus.HEALTHY
    assert battery.test_id is None
    assert any(item.category == "recovery" for item in recovered.activity)


def test_latched_fault_requires_manual_clear_and_records_recovery() -> None:
    simulator = service()
    simulator.step()
    started = simulator.start_fault(FaultRequest("st-4", FaultType.DEVICE, duration_steps=1))
    test = started.fault_tests[0]

    active = simulator.step()
    assert device(active, "st-4").test_id == test.test_id
    still_active = simulator.step()
    assert fault_state(test, still_active.sequence) is FaultTestState.ACTIVE
    assert device(still_active, "st-4").test_id == test.test_id

    cleared = simulator.clear_fault(test.test_id)
    assert fault_state(cleared.fault_tests[0], cleared.sequence) is FaultTestState.CLEARED
    recovered = simulator.step()
    assert device(recovered, "st-4").test_id is None
    assert any(
        item.test_id == test.test_id and item.category == "recovery" for item in recovered.activity
    )


def test_value_fault_report_and_replay_are_stable() -> None:
    simulator = service()
    simulator.step()
    simulator.start_fault(
        FaultRequest(
            "st-4",
            FaultType.RANGE,
            point_id="st-4.actual.power",
            test_value=9000,
        )
    )
    active = simulator.step()
    point = next(
        item for item in device(active, "st-4").values if item.logical_id == "st-4.actual.power"
    )
    assert point.value == 9000
    assert point.quality is DataQuality.OUT_OF_RANGE

    report = simulator.report_bytes()
    assert report == simulator.report_bytes()
    parsed = json.loads(report)
    assert parsed["tests"][0]["test_id"] == "test-0001"
    assert parsed["tests"][0]["state"] == "active"
    assert parsed["limitations"]

    expected_hash = active.trace_hash
    replayed = simulator.replay()
    assert replayed.trace_hash == expected_hash
    assert replayed.devices == active.devices


def test_fault_validation_rejects_overlap_unsupported_pairs_and_direct_bypass() -> None:
    simulator = service()
    simulator.step()
    simulator.start_fault(FaultRequest("ba1-1", FaultType.COMMUNICATION))

    with pytest.raises(ValueError, match="clear the current test"):
        simulator.start_fault(FaultRequest("ba1-1", FaultType.DEVICE))
    with pytest.raises(ValueError, match="not available"):
        simulator.start_fault(FaultRequest("dc600", FaultType.DERATE))
    with pytest.raises(ValueError, match="Fault testing"):
        simulator.set_inputs("ba1-1", BatteryInputs(communication_ok=False))


def test_reset_clears_the_fault_session_and_restarts_stable_ids() -> None:
    simulator = service()
    simulator.start_fault(FaultRequest("ba1-1", FaultType.COMMUNICATION))

    reset = simulator.reset()
    assert reset.fault_tests == ()
    restarted = simulator.start_fault(FaultRequest("ba1-1", FaultType.COMMUNICATION))
    assert restarted.fault_tests[0].test_id == "test-0001"


def test_snapshot_restores_pending_inputs_faults_and_continuation() -> None:
    simulator = service()
    simulator.step()
    simulator.set_inputs("ba1-1", BatteryInputs(cluster_soc=0.8))
    simulator.start_fault(
        FaultRequest("ba1-1", FaultType.COMMUNICATION, delay_steps=1, duration_steps=1)
    )
    saved = simulator.save_snapshot()
    expected = [simulator.step() for _ in range(4)]
    simulator.reset()
    restored = simulator.restore()
    assert restored == replace(saved, restored_run=saved.saved_run)
    assert [simulator.step() for _ in range(4)] == expected
    assert simulator.replay().plant_trace_hash == expected[-1].plant_trace_hash


def test_restore_and_profile_validation_do_not_change_the_run() -> None:
    simulator = service()
    before = simulator.state()
    with pytest.raises(ValueError, match="no simulation snapshot"):
        simulator.restore()
    with pytest.raises(ValueError, match="unknown plant profile"):
        simulator.set_profile("missing", 1)
    assert simulator.state() == before
    simulator.save_snapshot()
    simulator.start()
    with pytest.raises(ValueError, match="pause"):
        simulator.restore()
    with pytest.raises(ValueError, match="pause"):
        simulator.set_profile("stage0", 1)
    simulator.pause()
    changed = simulator.set_profile("stage0", 9)
    assert changed.sequence == 0
    assert changed.plant.configuration.seed == 9
    assert changed.snapshot_saved
    assert simulator.restore().plant.configuration.seed == before.plant.configuration.seed


def test_replay_mismatch_keeps_the_current_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from skid2.sim.runtime import SimulatorRuntime

    simulator = service()
    simulator.step()
    before = simulator._runtime.snapshot()
    original = SimulatorRuntime.plant_trace_bytes
    active = simulator._runtime
    monkeypatch.setattr(
        SimulatorRuntime,
        "plant_trace_bytes",
        lambda self: original(self) if self is active else b"wrong",
    )
    result = simulator.replay()
    assert result.failure is not None and result.failure.code == "replay_mismatch"
    assert simulator._runtime.snapshot() == before


def test_tick_and_requests_share_one_runtime() -> None:
    from concurrent.futures import ThreadPoolExecutor

    simulator = service()
    simulator.tick()
    assert simulator.state().sequence == 0
    simulator.start()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: simulator.tick(), range(20)))
    simulator.pause()
    simulator.tick()
    state = simulator.state()
    assert state.sequence == state.plant.sequence == 20
    assert all(port.sequence == 20 for port in state.plant.ports)
    assert simulator.replay().plant_trace_hash == state.plant_trace_hash


def test_plant_metadata_is_present_in_report_and_trace() -> None:
    simulator = service()
    state = simulator.step()
    report = json.loads(simulator.report_bytes())
    assert report["schema_version"] == 2
    assert report["plant_trace_hash"] == state.plant_trace_hash
    for config in [
        report["plant"]["configuration"],
        report["plant_trace"][0]["state"]["configuration"],
    ]:
        assert config["seed"] == 0 and config["revision"] == "stage0-3"
        assert config["review_owner"] and config["reviewers"]
        assert all(
            cap["source_ids"] and cap["review_status"] and cap["fidelity_level"]
            for cap in config["capabilities"]
        )
