"""EMS auth, config, device manager, charger, and supervisor tests."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import SecretStr

from skid2.application.simulation import SimulationService
from skid2.contracts import ManualClock
from skid2.ems.auth import hash_password, verify_password
from skid2.ems.charger import ChargerAvailability, ChargerMode
from skid2.ems.config import ConfigService
from skid2.ems.device_manager import DeviceManager
from skid2.ems.groups import config_spec
from skid2.ems.hub import EmsHub
from skid2.ems.supervisor import Supervisor
from skid2.plant.profiles import INITIAL_AT
from skid2.settings import Settings


def test_password_round_trip() -> None:
    stored = hash_password("operator")
    assert verify_password("operator", stored)
    assert not verify_password("wrong", stored)


def test_bms_pack_max_voltage_spec() -> None:
    spec = config_spec("bms", "pack_max_voltage")
    assert spec.default == 730.0
    assert spec.minimum == 600.0
    assert spec.maximum == 900.0


class MemoryConfigEngine:
    """Minimal stand-in used only when DB is unavailable in unit tests."""


@pytest.mark.anyio
async def test_device_manager_publishes_soc_and_zeros_on_comms_loss() -> None:
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
    points = await manager.poll_once()
    soc = next(point for point in points if point.point_id == "bess.cluster.soc")
    assert soc.stale is False
    assert soc.value > 0
    manager.inject_comms_loss("ba1-1")
    lost = await manager.poll_once()
    soc_lost = next(point for point in lost if point.point_id == "bess.cluster.soc")
    assert soc_lost.stale is True
    assert soc_lost.value == 0.0
    manager.clear_comms_loss("ba1-1")
    restored = await manager.poll_once()
    soc_ok = next(point for point in restored if point.point_id == "bess.cluster.soc")
    assert soc_ok.stale is False


def test_charger_soc_gates() -> None:
    class FakeConfig(ConfigService):
        def __init__(self) -> None:
            self._values = {
                ("charger", "soc_limited"): 0.15,
                ("charger", "soc_maintenance"): 0.15,
                ("charger", "soc_terminate"): 0.10,
                ("charger", "soc_recover"): 0.22,
                ("charger", "session_cap_kwh"): 10.0,
                ("charger", "station_message"): "Welcome",
            }

        def numeric(self, group_id: str, key: str) -> float:  # type: ignore[override]
            return float(self._values[(group_id, key)])

        def text(self, group_id: str, key: str) -> str:  # type: ignore[override]
            return str(self._values[(group_id, key)])

    availability = ChargerAvailability(FakeConfig())  # type: ignore[arg-type]
    assert availability.evaluate(0.5).mode is ChargerMode.NORMAL
    unavailable = availability.evaluate(0.14)
    assert unavailable.mode is ChargerMode.MAINTENANCE
    assert unavailable.allow_new_sessions is False
    terminated = availability.evaluate(0.09)
    assert terminated.mode is ChargerMode.TERMINATED
    still = availability.evaluate(0.16)
    assert still.mode is ChargerMode.TERMINATED
    recovered = availability.evaluate(0.22)
    assert recovered.mode is ChargerMode.RECOVERING


@pytest.mark.anyio
async def test_supervisor_heartbeats_and_status() -> None:
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

    class FakeConfig:
        def numeric(self, group_id: str, key: str) -> float:
            defaults = {
                ("supervisor", "bus_voltage_setpoint"): 750.0,
                ("supervisor", "available_power_reserve"): 20.0,
            }
            return defaults[(group_id, key)]

    supervisor = Supervisor(hub, FakeConfig())  # type: ignore[arg-type]
    snap = supervisor.snapshot()
    assert snap.heartbeats_total == 7
    assert snap.heartbeats_ok == 7
    assert snap.system_status in {3, 4}


def test_settings_include_ems_defaults() -> None:
    settings = Settings(
        environment="test",
        service_name="test",
        service_host="127.0.0.1",
        service_port=8000,
        database_url=SecretStr(
            "postgresql+psycopg://user:fake-test-value@db:5432/skid2"  # pragma: allowlist secret
        ),
    )
    assert settings.ems_enabled is True
    assert settings.data_source == "sim"
    assert settings.modbus_dc600_id == 4
