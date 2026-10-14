"""Fault-matrix regression coverage for EMS device manager (Week 7)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from skid2.application.simulation import SimulationService
from skid2.contracts import ManualClock
from skid2.ems.device_manager import DeviceManager
from skid2.ems.groups import DEVICE_IDS
from skid2.ems.hub import EmsHub
from skid2.plant.profiles import INITIAL_AT
from skid2.sim.faults import FAULT_RULES


@pytest.mark.anyio
async def test_comms_loss_matrix_all_devices() -> None:
    hub = EmsHub()
    manager = DeviceManager(hub, None, data_source="sim")
    simulation = SimulationService(
        ManualClock(INITIAL_AT),
        timedelta(seconds=1),
        "protocol",
        seed=0,
        profile_id="stage0",
    )
    simulation.step()
    manager.attach_simulation(simulation.values)
    await manager.poll_once()
    for device_id in DEVICE_IDS:
        manager.inject_comms_loss(device_id)
        points = await manager.poll_once()
        device_points = [point for point in points if point.device_id == device_id]
        assert device_points
        assert all(point.stale and point.value == 0.0 for point in device_points)
        manager.clear_comms_loss(device_id)
        restored = await manager.poll_once()
        assert hub.heartbeats.get(device_id) is True
        assert any(point.device_id == device_id and not point.stale for point in restored)


def test_fault_rule_catalog_covers_six_types() -> None:
    kinds = {rule.fault_type for rule in FAULT_RULES}
    assert len(FAULT_RULES) >= 6
    assert len(kinds) >= 6
