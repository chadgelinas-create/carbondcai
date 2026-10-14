"""Week 5 Stabiliti fleet split and operational labels."""

from datetime import UTC, datetime, timedelta

import pytest

from skid2.application.simulation import SimulationService
from skid2.contracts import DataQuality, ManualClock
from skid2.ems.device_manager import DeviceManager
from skid2.ems.hub import EmsHub, LivePoint
from skid2.ems.stabiliti_fleet import operational_label, publish_state_events, split_site_load
from skid2.plant.profiles import INITIAL_AT


def test_site_load_splits_across_online_units() -> None:
    loads = split_site_load(
        90.0,
        {"st-4": 60.0, "st-5": 60.0, "st-6": 60.0},
        {"st-4": True, "st-5": True, "st-6": True},
    )
    assert loads == {"st-4": 30.0, "st-5": 30.0, "st-6": 30.0}


def test_offline_unit_gets_no_share() -> None:
    loads = split_site_load(
        60.0,
        {"st-4": 60.0, "st-5": 60.0, "st-6": 60.0},
        {"st-4": True, "st-5": False, "st-6": False},
    )
    assert loads["st-4"] == 60.0
    assert loads["st-5"] == 0.0
    assert loads["st-6"] == 0.0


def test_operational_labels() -> None:
    assert operational_label(0) == "offline"
    assert operational_label(2) == "enabled"
    assert operational_label(5) == "recovering"


class _Hub:
    def __init__(self, point: LivePoint) -> None:
        self._point = point

    def get(self, point_id: str) -> LivePoint | None:
        return self._point if point_id == self._point.point_id else None


def test_state_events_fire_once_per_change() -> None:
    hub = _Hub(
        LivePoint(
            "st-4.operational.state",
            "st-4",
            "stabiliti",
            "op",
            2.0,
            "enum",
            DataQuality.GOOD.value,
            datetime(2026, 10, 13, 16, tzinfo=UTC),
            False,
        )
    )
    seen: list[str] = []
    publish_state_events(hub, lambda _category, message, _device_id: seen.append(message))
    publish_state_events(hub, lambda _category, message, _device_id: seen.append(message))
    assert seen == ["st-4 operational state enabled"]


def test_stale_operational_state_is_offline_event() -> None:
    hub = _Hub(
        LivePoint(
            "st-6.operational.state",
            "st-6",
            "stabiliti",
            "op",
            0.0,
            "enum",
            DataQuality.COMM_ERROR.value,
            datetime(2026, 10, 13, 16, tzinfo=UTC),
            True,
        )
    )
    seen: list[str] = []
    publish_state_events(hub, lambda _category, message, _device_id: seen.append(message))
    assert seen == ["st-6 operational state offline"]


@pytest.mark.anyio
async def test_st4_comms_loss_zeros_ac_dc_and_state() -> None:
    hub = EmsHub()
    manager = DeviceManager(hub, repository=None, data_source="sim", poll_seconds=1.0)
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
    manager.inject_comms_loss("st-4")
    lost = await manager.poll_once()
    by_id = {point.point_id: point for point in lost if point.device_id == "st-4"}
    for point_id in (
        "st-4.ac.port.power",
        "st-4.dc.in.power",
        "st-4.ac.port.volts",
        "st-4.operational.state",
        "st-4.communication.ok",
    ):
        assert by_id[point_id].stale is True
        assert by_id[point_id].value == 0.0
    manager.clear_comms_loss("st-4")
    restored = await manager.poll_once()
    assert any(
        point.point_id == "st-4.operational.state" and not point.stale for point in restored
    )
