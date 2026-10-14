"""Protected fault-test API contract tests."""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from skid2.application.simulation import SimulationService
from skid2.backend import app as app_module
from skid2.backend import simulator as simulator_module
from skid2.contracts import ManualClock
from skid2.settings import Settings
from skid2.sim.battery import BatteryInputs
from skid2.sim.converter import ConverterInputs
from skid2.sim.dc600 import Dc600Inputs
from skid2.sim.stabiliti import StabilitiInputs

pytestmark = pytest.mark.usefixtures("legacy_api")

NOW = datetime(2026, 8, 1, 10, tzinfo=UTC)


def settings(*, simulation_enabled: bool = True) -> Settings:
    return Settings(
        environment="test",
        service_name="test",
        service_host="127.0.0.1",
        service_port=8000,
        database_url=SecretStr(
            "postgresql+psycopg://user:fake-test-value@db:5432/skid2"  # pragma: allowlist secret
        ),
        simulation_enabled=simulation_enabled,
        simulation_seed=0,
        protocol_root=Path("protocol"),
    )


@pytest.fixture
def simulation(monkeypatch: pytest.MonkeyPatch) -> SimulationService:
    service = SimulationService(ManualClock(NOW), timedelta(seconds=1), Path("protocol"), seed=0)
    app_module.app.state.simulation = service
    monkeypatch.setattr(simulator_module, "get_settings", settings)
    return service


@pytest.mark.anyio
async def test_fault_catalog_start_clear_and_report(simulation: SimulationService) -> None:
    simulation.step()
    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        catalog = await client.get("/api/v1/simulation/faults")
        started = await client.post(
            "/api/v1/simulation/faults",
            json={
                "device_id": "ba1-1",
                "fault_type": "communication_loss",
                "point_id": None,
                "test_value": None,
                "delay_steps": 0,
                "duration_steps": None,
            },
        )
        cleared = await client.post("/api/v1/simulation/faults/test-0001/clear")
        report = await client.get("/api/v1/simulation/report")

    assert catalog.status_code == 200
    assert len(catalog.json()["rules"]) == 7
    assert started.status_code == 200
    assert started.json()["fault_tests"][0]["state"] == "scheduled"
    assert cleared.status_code == 200
    assert cleared.json()["fault_tests"][0]["state"] == "cleared"
    assert report.status_code == 200
    assert report.headers["content-disposition"] == 'attachment; filename="simulator-report.json"'
    assert report.json()["tests"][0]["test_id"] == "test-0001"


@pytest.mark.anyio
async def test_fault_api_rejects_unknown_and_extra_inputs(simulation: SimulationService) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        unknown = await client.post(
            "/api/v1/simulation/faults",
            json={"device_id": "ba1-1", "fault_type": "not-a-real-problem"},
        )
        extra = await client.post(
            "/api/v1/simulation/faults",
            json={
                "device_id": "ba1-1",
                "fault_type": "communication_loss",
                "raw_register": 12295,
            },
        )
        missing = await client.post("/api/v1/simulation/faults/test-9999/clear")

    assert unknown.status_code == 400
    assert unknown.json() == {"detail": "unknown problem to test"}
    assert extra.status_code == 422
    assert missing.status_code == 404


@pytest.mark.anyio
async def test_fault_routes_are_unavailable_when_simulation_is_disabled(
    simulation: SimulationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        simulator_module,
        "get_settings",
        lambda: settings(simulation_enabled=False),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        responses = (
            await client.get("/api/v1/simulation/faults"),
            await client.post(
                "/api/v1/simulation/faults",
                json={"device_id": "ba1-1", "fault_type": "communication_loss"},
            ),
            await client.post("/api/v1/simulation/faults/test-0001/clear"),
            await client.get("/api/v1/simulation/report"),
        )

    assert {response.status_code for response in responses} == {404}
    assert all(response.json() == {"detail": "simulation is unavailable"} for response in responses)


@pytest.mark.anyio
async def test_saved_snapshot_restores_device_and_plant(simulation: SimulationService) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        first = await client.post("/api/v1/simulation/step")
        saved = await client.post("/api/v1/simulation/snapshot")
        moved = await client.post("/api/v1/simulation/step")
        restored = await client.post("/api/v1/simulation/restore")
        replayed = await client.post("/api/v1/simulation/replay")
        profiles = await client.get("/api/v1/simulation/profiles")
        view = await client.get("/api/v1/simulation/snapshot")
    assert all(
        response.status_code == 200
        for response in (first, saved, moved, restored, replayed, profiles, view)
    )
    assert moved.json()["sequence"] == 2
    assert restored.json()["snapshot_saved"] is True
    for field in ("sequence", "devices", "plant", "trace_hash", "plant_trace_hash", "bus_history"):
        assert restored.json()[field] == saved.json()[field] == replayed.json()[field]
    assert profiles.json()["profiles"] == [
        "stage0",
        "solar_step",
        "solar_ramp",
        "solar_daylight",
        "solar_cloud",
        "solar_variable",
    ]
    assert view.json()["plant"]["configuration"]["seed"] == 0


@pytest.mark.anyio
async def test_profile_and_restore_reject_invalid_requests(simulation: SimulationService) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        missing = await client.post("/api/v1/simulation/restore")
        invalid = await client.post(
            "/api/v1/simulation/profile", json={"profile_id": "missing", "seed": 3}
        )
        bad_seed = await client.post(
            "/api/v1/simulation/profile", json={"profile_id": "stage0", "seed": True}
        )
        await client.post("/api/v1/simulation/start")
        running = await client.post(
            "/api/v1/simulation/profile", json={"profile_id": "stage0", "seed": 3}
        )
        await client.post("/api/v1/simulation/pause")
        selected = await client.post(
            "/api/v1/simulation/profile", json={"profile_id": "stage0", "seed": 3}
        )
    assert missing.status_code == invalid.status_code == running.status_code == 409
    assert bad_seed.status_code == 422
    assert selected.status_code == 200
    assert selected.json()["sequence"] == 0
    assert selected.json()["plant"]["configuration"]["seed"] == 3


@pytest.mark.anyio
async def test_all_plant_controls_fail_closed_in_production(
    simulation: SimulationService, monkeypatch: pytest.MonkeyPatch
) -> None:
    production = settings(simulation_enabled=False).model_copy(update={"environment": "production"})
    monkeypatch.setattr(simulator_module, "get_settings", lambda: production)
    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        responses = [
            await client.post(f"/api/v1/simulation/{action}")
            for action in ("start", "pause", "step", "reset", "snapshot", "restore", "replay")
        ]
        responses += [
            await client.get("/api/v1/simulation/profiles"),
            await client.post(
                "/api/v1/simulation/profile", json={"profile_id": "stage0", "seed": 0}
            ),
        ]
    assert {response.status_code for response in responses} == {404}
    assert simulation.state().sequence == 0


@pytest.mark.anyio
async def test_backend_loop_advances_only_while_running(simulation: SimulationService) -> None:
    import asyncio

    stop = asyncio.Event()
    task = asyncio.create_task(app_module._run_simulation(simulation, stop, 0.005))
    try:
        await asyncio.sleep(0.02)
        assert simulation.state().sequence == 0
        simulation.start()
        async with asyncio.timeout(2):
            while simulation.state().sequence < 2:
                await asyncio.sleep(0.005)
        paused = simulation.pause()
        await asyncio.sleep(0.02)
        assert simulation.state().sequence == paused.sequence
        stepped = simulation.step()
        assert stepped.sequence == paused.sequence + 1
        assert stepped.at == stepped.plant.at
    finally:
        stop.set()
        await task
    assert task.done()


@pytest.mark.anyio
async def test_bus_input_and_calculated_history_api(simulation: SimulationService) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        for invalid in (-1, True, "5"):
            response = await client.post(
                "/api/v1/simulation/devices/st-4/inputs",
                json={**asdict(StabilitiInputs()), "kind": "stabiliti", "bus_load": invalid},
            )
            assert response.status_code == 422
        for device_id in ("st-4", "st-5", "st-6"):
            response = await client.post(
                f"/api/v1/simulation/devices/{device_id}/inputs",
                json={**asdict(StabilitiInputs()), "kind": "stabiliti", "bus_load": 0},
            )
            assert response.status_code == 200
        dc = await client.post(
            "/api/v1/simulation/devices/dc600/inputs",
            json={
                "kind": "dc600",
                "demand_power": 0,
                "available_power": 100,
                "demand_fresh": True,
                "available_fresh": True,
                "session_active": False,
                "communication_ok": True,
                "fault_active": False,
            },
        )
        assert dc.status_code == 200
        converter = await client.post(
            "/api/v1/simulation/devices/cnvtr-2/inputs",
            json={
                **asdict(ConverterInputs()),
                "kind": "converter",
                "enabled": True,
                "pv_available": True,
                "requested_power": 10,
                "available_power": 100,
            },
        )
        assert converter.status_code == 200
        response = await client.post("/api/v1/simulation/step")
        assert response.status_code == 200
        body = response.json()
        assert body["plant"]["balance"] == "available"
        assert body["bus_history"] == [
            {"at": body["at"], "sequence": 1, "values": body["plant"]["bus"]}
        ]
        assert body["plant"]["bus"][0]["value"] ** 2 == pytest.approx(582500)
        report = (await client.get("/api/v1/simulation/report")).json()
        assert len(report["plant_trace"]) == 1
        reset = (await client.post("/api/v1/simulation/reset")).json()
        assert reset["bus_history"] == []
        assert reset["plant"]["balance"] == "missing"


@pytest.mark.anyio
async def test_solar_profile_api_exposes_paths_history_and_replay(
    simulation: SimulationService,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        selected = await client.post(
            "/api/v1/simulation/profile", json={"profile_id": "solar_cloud", "seed": 7}
        )
        assert selected.status_code == 200
        first = await client.post("/api/v1/simulation/step")
        state = first.json()
        assert state["failure"] is None
        assert [item["device_id"] for item in state["plant"]["pv_paths"]] == [
            "cnvtr-2",
        ]
        assert len(state["pv_history"]) == 1
        await client.post("/api/v1/simulation/snapshot")
        await client.post("/api/v1/simulation/step")
        restored = (await client.post("/api/v1/simulation/restore")).json()
        assert restored["plant"] == state["plant"]
        replayed = (await client.post("/api/v1/simulation/replay")).json()
        assert replayed["plant_trace_hash"] == state["plant_trace_hash"]
        assert replayed["pv_history"] == state["pv_history"]


@pytest.mark.anyio
async def test_battery_setup_energy_history_and_replay(simulation: SimulationService) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        selected = await client.post(
            "/api/v1/simulation/profile",
            json={"profile_id": "stage0", "seed": 0, "battery_profile": "battery_3500"},
        )
        assert selected.status_code == 200
        assert selected.json()["plant"]["configuration"]["battery"]["capacity_kwh"] == 3500
        first = await client.post("/api/v1/simulation/step")
        body = first.json()
        assert body["failure"] is None
        assert body["battery_history"][0]["values"] == body["plant"]["bess"]
        assert body["plant"]["bess"][0]["value"] == 1750
        await client.post("/api/v1/simulation/snapshot")
        await client.post("/api/v1/simulation/step")
        restored = await client.post("/api/v1/simulation/restore")
        assert restored.json()["battery_history"] == body["battery_history"]
        replayed = await client.post("/api/v1/simulation/replay")
        assert replayed.json()["plant_trace_hash"] == body["plant_trace_hash"]
        invalid = await client.post(
            "/api/v1/simulation/profile",
            json={"profile_id": "stage0", "seed": 0, "battery_profile": "guess"},
        )
        assert invalid.status_code == 409
        unchanged = await client.get("/api/v1/simulation")
        assert unchanged.json()["plant"] == body["plant"]


@pytest.mark.anyio
@pytest.mark.parametrize("custom", [False, True])
async def test_assumed_battery_cannot_be_selected_in_production(
    simulation: SimulationService, monkeypatch: pytest.MonkeyPatch, custom: bool
) -> None:
    production = settings(simulation_enabled=False).model_copy(update={"environment": "production"})
    monkeypatch.setattr(simulator_module, "get_settings", lambda: production)
    before = simulation.state()
    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/simulation/profile",
            json={
                "profile_id": "stage0",
                "seed": 0,
                "battery_profile": "custom" if custom else "battery_3500",
                "battery_settings": CUSTOM_BATTERY if custom else None,
            },
        )
    assert response.status_code == 404
    assert simulation.state() == before


CUSTOM_BATTERY = {
    "capacity_kwh": 3500,
    "initial_level_percent": 80,
    "charge_efficiency_percent": 80,
    "discharge_efficiency_percent": 90,
    "charge_limit_kw": 200,
    "discharge_limit_kw": 250,
}

CUSTOM_BUS = {
    "nominal_voltage_v": 800,
    "max_voltage_v": 900,
    "capacitance_f": 2,
    "loss_percent": 10,
    "tolerance_kw": 0.00001,
}


@pytest.mark.anyio
async def test_custom_bus_power_limits_and_saved_state(simulation: SimulationService) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        selected = await client.post(
            "/api/v1/simulation/profile",
            json={
                "profile_id": "solar_cloud",
                "seed": 0,
                "battery_profile": "custom",
                "battery_settings": CUSTOM_BATTERY,
                "bus_settings": CUSTOM_BUS,
            },
        )
        assert selected.status_code == 200
        body = selected.json()
        config = body["plant"]["configuration"]["bus"]
        assert config == {
            "nominal_voltage_v": 800,
            "max_voltage_v": 900,
            "capacitance_f": 2,
            "loss_ratio": 0.1,
            "tolerance_kw": 0.00001,
        }
        assert body["plant"]["bus"][0]["value"] == 800
        initial_energy = 2 * 800**2 / 7_200_000
        assert body["plant"]["bus"][1]["value"] == pytest.approx(initial_energy)
        stepped = (await client.post("/api/v1/simulation/step")).json()
        assert stepped["failure"] is None
        ledger = {item["name"]: item["value"] for item in stepped["plant"]["ledger"]}
        assert ledger["Power supplied"] == 95
        assert ledger["Power used"] == 60
        assert ledger["Modeled loss"] == 9.5
        assert ledger["Energy change"] == pytest.approx(25.5 / 3600)
        energy = initial_energy + 25.5 / 3600
        assert stepped["plant"]["bus"][1]["value"] == pytest.approx(energy)
        assert stepped["plant"]["bus"][0]["value"] == pytest.approx((energy * 7_200_000 / 2) ** 0.5)
        await client.post("/api/v1/simulation/snapshot")
        await client.post("/api/v1/simulation/step")
        restored = (await client.post("/api/v1/simulation/restore")).json()
        assert restored["plant"] == stepped["plant"]
        replayed = (await client.post("/api/v1/simulation/replay")).json()
        assert replayed["plant_trace_hash"] == stepped["plant_trace_hash"]
        report = (await client.get("/api/v1/simulation/report")).json()
        assert report["plant"]["configuration"]["bus"] == config
        reset = (await client.post("/api/v1/simulation/reset")).json()
        assert reset["plant"]["bus"][0]["value"] == 800
        assert reset["plant"]["bus"][1]["value"] == pytest.approx(initial_energy)
        assert reset["plant"]["configuration"]["bus"] == config
        assert reset["bus_history"] == []
        await client.post("/api/v1/simulation/start")
        before = simulation.state().plant
        rejected = await client.post(
            "/api/v1/simulation/profile",
            json={"profile_id": "stage0", "seed": 0, "bus_settings": CUSTOM_BUS},
        )
        assert rejected.status_code == 409
        assert simulation.state().plant == before
        await client.post("/api/v1/simulation/pause")
        capped = await client.post(
            "/api/v1/simulation/profile",
            json={
                "profile_id": "solar_cloud",
                "seed": 0,
                "bus_settings": {**CUSTOM_BUS, "max_voltage_v": 800},
            },
        )
        assert capped.status_code == 200
        limited = (await client.post("/api/v1/simulation/step")).json()
        assert limited["failure"] is None
        assert limited["plant"]["bus"][0]["value"] == pytest.approx(800)
        ledger = {item["name"]: item["value"] for item in limited["plant"]["ledger"]}
        assert ledger["Power supplied"] == pytest.approx(60 / 0.9)
        assert ledger["Unused supply"] == pytest.approx(95 - 60 / 0.9)
        legacy = await client.post(
            "/api/v1/simulation/profile", json={"profile_id": "solar_cloud", "seed": 0}
        )
        assert legacy.status_code == 200
        assert legacy.json()["plant"]["configuration"]["bus"] == {
            "nominal_voltage_v": 750,
            "max_voltage_v": 1500,
            "capacitance_f": 1,
            "loss_ratio": 0,
            "tolerance_kw": 0.000001,
        }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("changes", "status_code"),
    [
        ({"nominal_voltage_v": 0}, 422),
        ({"nominal_voltage_v": 901}, 409),
        ({"max_voltage_v": 0}, 422),
        ({"capacitance_f": -1}, 422),
        ({"capacitance_f": "2"}, 422),
        ({"capacitance_f": True}, 422),
        ({"loss_percent": -1}, 422),
        ({"loss_percent": 100}, 422),
        ({"tolerance_kw": 0}, 422),
        ({"max_voltage_v": None}, 422),
        ({"max_voltage_v": 1e308}, 409),
        ({"extra": 1}, 422),
    ],
)
async def test_invalid_bus_settings_keep_the_current_run(
    simulation: SimulationService, changes: dict[str, object], status_code: int
) -> None:
    before = simulation.save_snapshot()
    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/simulation/profile",
            json={
                "profile_id": "stage0",
                "seed": 0,
                "bus_settings": {**CUSTOM_BUS, **changes},
            },
        )
    assert response.status_code == status_code
    assert simulation.state() == before


@pytest.mark.anyio
async def test_custom_battery_settings_power_and_saved_state(simulation: SimulationService) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        selected = await client.post(
            "/api/v1/simulation/profile",
            json={
                "profile_id": "stage0",
                "seed": 0,
                "battery_profile": "custom",
                "battery_settings": CUSTOM_BATTERY,
            },
        )
        assert selected.status_code == 200
        body = selected.json()
        config = body["plant"]["configuration"]["battery"]
        assert config == {
            "profile_id": "custom",
            "capacity_kwh": 3500,
            "initial_energy_kwh": 2800,
            "charge_efficiency": 0.8,
            "discharge_efficiency": 0.9,
            "charge_limit_kw": 200,
            "discharge_limit_kw": 250,
            "revision": "battery-1",
        }
        devices = {device.device_id: device for device in simulation.state().devices}
        bms = devices["ba1-1"].inputs
        converter = devices["cnvtr-1"].inputs
        assert isinstance(bms, BatteryInputs)
        assert isinstance(converter, ConverterInputs)
        assert (bms.charge_power_limit, bms.discharge_power_limit) == (200, 250)
        assert (converter.charge_power_limit, converter.discharge_power_limit) == (200, 250)
        assert converter.available_power == 250
        simulation.set_inputs(
            "cnvtr-1", ConverterInputs(**{**asdict(converter), "requested_power": 200})
        )
        simulation.set_inputs("dc600", Dc600Inputs(demand_power=200, available_power=200))
        discharged = (await client.post("/api/v1/simulation/step")).json()
        assert discharged["failure"] is None
        values = {value["name"]: value["value"] for value in discharged["plant"]["bess"]}
        assert values["Actual power"] == 200
        assert values["Battery energy"] == pytest.approx(2800 - 200 / 0.9 / 3600)
        assert values["Energy loss rate"] == pytest.approx(200 / 0.9 - 200)
        await client.post("/api/v1/simulation/snapshot")
        await client.post("/api/v1/simulation/step")
        restored = (await client.post("/api/v1/simulation/restore")).json()
        replayed = (await client.post("/api/v1/simulation/replay")).json()
        assert restored["plant"] == discharged["plant"]
        assert replayed["plant_trace_hash"] == discharged["plant_trace_hash"]
        report = (await client.get("/api/v1/simulation/report")).json()
        assert config == report["plant"]["configuration"]["battery"]
        simulation.set_inputs(
            "ba1-1", BatteryInputs(**{**asdict(bms), "discharge_power_limit": 20})
        )
        limited = (await client.post("/api/v1/simulation/step")).json()
        assert limited["failure"] is None
        assert (
            next(
                value["value"]
                for value in limited["plant"]["bess"]
                if value["name"] == "Actual power"
            )
            == 20
        )
        reset = (await client.post("/api/v1/simulation/reset")).json()
        assert reset["plant"]["bess"][0]["value"] == 2800
        assert reset["plant"]["configuration"]["battery"] == config
        assert reset["battery_history"] == []
        unchanged = simulation.state()
        await client.post("/api/v1/simulation/start")
        rejected = await client.post(
            "/api/v1/simulation/profile",
            json={
                "profile_id": "stage0",
                "seed": 0,
                "battery_profile": "custom",
                "battery_settings": {**CUSTOM_BATTERY, "capacity_kwh": 4000},
            },
        )
        assert rejected.status_code == 409
        assert simulation.state().plant == unchanged.plant
        await client.post("/api/v1/simulation/pause")
        legacy = (
            await client.post(
                "/api/v1/simulation/profile", json={"profile_id": "stage0", "seed": 0}
            )
        ).json()
        assert legacy["plant"]["configuration"]["battery"] is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capacity_kwh", 0),
        ("capacity_kwh", "3500"),
        ("capacity_kwh", True),
        ("initial_level_percent", -1),
        ("initial_level_percent", 101),
        ("charge_efficiency_percent", 0),
        ("discharge_efficiency_percent", 101),
        ("charge_limit_kw", -1),
        ("discharge_limit_kw", -1),
        ("capacity_kwh", None),
    ],
)
async def test_invalid_custom_battery_keeps_the_current_run(
    simulation: SimulationService, field: str, value: object
) -> None:
    before = simulation.save_snapshot()
    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/simulation/profile",
            json={
                "profile_id": "stage0",
                "seed": 0,
                "battery_profile": "custom",
                "battery_settings": {**CUSTOM_BATTERY, field: value},
            },
        )
    assert response.status_code == 422
    assert simulation.state() == before


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("profile", "settings_value"),
    [("custom", None), ("battery_3500", CUSTOM_BATTERY), (None, CUSTOM_BATTERY)],
)
async def test_custom_settings_cannot_be_ignored_or_guessed(
    simulation: SimulationService, profile: str | None, settings_value: dict[str, int] | None
) -> None:
    before = simulation.state()
    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/simulation/profile",
            json={
                "profile_id": "stage0",
                "seed": 0,
                "battery_profile": profile,
                "battery_settings": settings_value,
            },
        )
    assert response.status_code == 409
    assert simulation.state() == before


@pytest.mark.anyio
async def test_custom_battery_charge_efficiency_and_zero_limits(
    simulation: SimulationService,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/simulation/profile",
            json={
                "profile_id": "solar_cloud",
                "seed": 0,
                "battery_profile": "custom",
                "battery_settings": {
                    **CUSTOM_BATTERY,
                    "capacity_kwh": 1000.5,
                    "initial_level_percent": 0,
                    "discharge_limit_kw": 0,
                },
            },
        )
        assert response.status_code == 200
        simulation.set_inputs(
            "cnvtr-1",
            ConverterInputs(
                enabled=True,
                requested_power=-40,
                available_power=200,
                charge_power_limit=200,
                discharge_power_limit=0,
            ),
        )
        simulation.set_inputs("dc600", Dc600Inputs(demand_power=0, available_power=100))
        charged = (await client.post("/api/v1/simulation/step")).json()
        assert charged["failure"] is None
        values = {value["name"]: value["value"] for value in charged["plant"]["bess"]}
        assert values["Actual power"] == -40
        assert values["Battery energy"] == pytest.approx(40 * 0.8 / 3600)
        assert values["Energy loss rate"] == pytest.approx(8)
        full = await client.post(
            "/api/v1/simulation/profile",
            json={
                "profile_id": "stage0",
                "seed": 0,
                "battery_profile": "custom",
                "battery_settings": {
                    **CUSTOM_BATTERY,
                    "initial_level_percent": 100,
                    "charge_limit_kw": 0,
                    "discharge_limit_kw": 0,
                },
            },
        )
        assert full.status_code == 200
        idle = (await client.post("/api/v1/simulation/step")).json()
        assert idle["failure"] is None
        assert idle["plant"]["bess"][0]["value"] == 3500


@pytest.mark.anyio
async def test_saved_run_actions_and_pending_input_contract(simulation: SimulationService) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        simulation.set_inputs("ba1-1", BatteryInputs(cluster_soc=0.7))
        waiting = (await client.get("/api/v1/simulation")).json()
        battery = next(item for item in waiting["devices"] if item["device_id"] == "ba1-1")
        assert battery["inputs_pending"]
        assert battery["inputs"]["cluster_soc"] == 0.7
        assert battery["applied_inputs"]["cluster_soc"] == 0.5
        applied = (await client.post("/api/v1/simulation/step")).json()
        battery = next(item for item in applied["devices"] if item["device_id"] == "ba1-1")
        assert not battery["inputs_pending"]
        assert battery["applied_inputs"]["cluster_soc"] == 0.7
        saved = (await client.post("/api/v1/simulation/snapshot")).json()
        assert saved["saved_run"]["sequence"] == 1
        assert saved["saved_run"]["simulation_at"] == saved["at"]
        fresh = await client.post("/api/v1/simulation/new")
        assert fresh.status_code == 200
        assert fresh.json()["sequence"] == 0
        assert fresh.json()["snapshot_saved"]
        restored = (await client.post("/api/v1/simulation/restore")).json()
        assert restored["restored_run"] == saved["saved_run"]
        deleted = await client.delete("/api/v1/simulation/snapshot")
        assert deleted.status_code == 200
        assert deleted.json()["sequence"] == 1
        assert not deleted.json()["snapshot_saved"]
        assert deleted.json()["saved_run"] is None
        assert (await client.post("/api/v1/simulation/restore")).status_code == 409
        await client.post("/api/v1/simulation/start")
        assert (await client.post("/api/v1/simulation/new")).status_code == 409


@pytest.mark.anyio
async def test_failed_checkpoint_save_returns_error_without_claiming_success(
    simulation: SimulationService,
) -> None:
    from unittest.mock import Mock

    from skid2.persistence.simulation import SimulationStore, SimulationStoreError

    saved = simulation.save_snapshot()
    simulation.step()
    before = simulation.state()
    repository = Mock(spec=SimulationStore)
    repository.save.side_effect = SimulationStoreError(
        "Simulation data could not be saved. The previous save is unchanged."
    )
    repository.delete_checkpoint.side_effect = SimulationStoreError(
        "The saved run could not be deleted."
    )
    simulation._repository = repository
    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/simulation/snapshot")
        assert response.status_code == 503
        assert "previous save is unchanged" in response.json()["detail"]
        assert simulation.state() == before
        assert simulation.state().saved_run == saved.saved_run
        assert (await client.delete("/api/v1/simulation/snapshot")).status_code == 503
        assert simulation.state() == before
