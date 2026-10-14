"""Week 2 MPPT write-back and Week 4 charger session/handshake tests."""

from __future__ import annotations

import pytest

from skid2.ems.charger_handshake import HandshakeMachine, HandshakePhase
from skid2.ems.charger_sessions import ChargerSessionStore
from skid2.ems.charger_yard import ChargerYard
from skid2.ems.mppt import MpptController
from skid2.ems.setpoints import apply_sim_setpoints


class FakeConfig:
    def __init__(self) -> None:
        self._values = {
            ("solar", "mppt_hold"): 0.95,
            ("solar", "mppt_resume"): 0.90,
            ("solar", "mppt_taper"): 0.85,
            ("solar", "mppt_off"): 0.98,
            ("solar", "mppt_restart"): 0.80,
            ("cnvtr", "cnvtr1_power_limit"): 250.0,
            ("cnvtr", "cnvtr2_power_limit"): 300.0,
            ("stabiliti", "st4_power_limit"): 60.0,
            ("stabiliti", "st5_power_limit"): 60.0,
            ("stabiliti", "st6_power_limit"): 60.0,
            ("stabiliti", "site_ac_load"): 0.0,
        }

    def numeric(self, group_id: str, key: str) -> float:
        return float(self._values[(group_id, key)])


class FakeHub:
    def get(self, _point_id: str) -> None:
        return None


class FakeSimulation:
    def state(self) -> object:
        return type("State", (), {"devices": ()})()


def test_session_opens_and_closes_with_energy() -> None:
    yard = ChargerYard()
    store = ChargerSessionStore()
    yard.set_port("2", occupied=True, requested_kw=200.0, allow_new=True)
    record = store.open_session("2", 200.0)
    yard.bind_session("2", record.id)
    snap = yard.tick(3600.0, 200.0, allow_new=True, energy_cap_kwh=10.0)
    store.update_open(snap)
    port = next(item for item in snap.ports if item.port_id == "2")
    assert port.energy_kwh == pytest.approx(10.0) or port.capped is True
    closed = store.close_session("2", port.energy_kwh, capped=port.capped)
    assert closed is not None
    assert closed.status == "ended"
    assert store.open_records() == ()


def test_energy_cap_stops_allocation() -> None:
    yard = ChargerYard()
    yard.set_port("1", occupied=True, requested_kw=60.0, allow_new=True)
    snap = yard.tick(3600.0, 60.0, allow_new=True, energy_cap_kwh=10.0)
    port = snap.ports[0]
    assert port.capped is True
    assert port.allocated_kw == 0.0


def test_handshake_ack_then_charge_or_limit() -> None:
    machine = HandshakeMachine()
    assert (
        machine.step(
            occupied=True, demand_kw=200, available_kw=250, communication_ok=True, allocated_kw=0
        )
        is HandshakePhase.REQUESTED
    )
    assert (
        machine.step(
            occupied=True, demand_kw=200, available_kw=250, communication_ok=True, allocated_kw=200
        )
        is HandshakePhase.ACKNOWLEDGED
    )
    assert (
        machine.step(
            occupied=True, demand_kw=200, available_kw=250, communication_ok=True, allocated_kw=200
        )
        is HandshakePhase.CHARGING
    )
    assert (
        machine.step(
            occupied=True, demand_kw=200, available_kw=80, communication_ok=True, allocated_kw=80
        )
        is HandshakePhase.LIMITED
    )


def test_handshake_timeout_and_recovery() -> None:
    machine = HandshakeMachine()
    machine.step(
        occupied=True, demand_kw=60, available_kw=100, communication_ok=True, allocated_kw=60
    )
    machine.step(
        occupied=True, demand_kw=60, available_kw=100, communication_ok=True, allocated_kw=60
    )
    machine.step(
        occupied=True, demand_kw=60, available_kw=100, communication_ok=True, allocated_kw=60
    )
    assert (
        machine.step(
            occupied=True,
            demand_kw=60,
            available_kw=100,
            communication_ok=True,
            allocated_kw=60,
            fresh=False,
        )
        is HandshakePhase.TIMEOUT
    )
    assert (
        machine.step(
            occupied=True, demand_kw=60, available_kw=100, communication_ok=True, allocated_kw=60
        )
        is HandshakePhase.RECOVERING
    )


def test_mppt_off_latches_until_restart() -> None:
    mppt = MpptController()
    config = FakeConfig()  # type: ignore[arg-type]
    stopped = mppt.evaluate(0.99, 300.0, config)  # type: ignore[arg-type]
    assert stopped.applying is False
    assert stopped.requested_kw == 0.0
    still = mppt.evaluate(0.90, 300.0, config)  # type: ignore[arg-type]
    assert still.requested_kw == 0.0
    resumed = mppt.evaluate(0.80, 300.0, config)  # type: ignore[arg-type]
    assert resumed.applying is True
    assert resumed.requested_kw == 300.0


def test_apply_setpoints_with_no_devices_is_noop() -> None:
    notes = apply_sim_setpoints(
        FakeSimulation(),  # type: ignore[arg-type]
        FakeConfig(),  # type: ignore[arg-type]
        FakeHub(),  # type: ignore[arg-type]
        MpptController(),
    )
    assert notes == []


def test_mppt_taper_and_hold() -> None:
    mppt = MpptController()
    config = FakeConfig()  # type: ignore[arg-type]
    taper = mppt.evaluate(0.86, 200.0, config)  # type: ignore[arg-type]
    assert taper.requested_kw == 100.0
    hold = mppt.evaluate(0.96, 200.0, config)  # type: ignore[arg-type]
    assert hold.applying is False
    assert hold.requested_kw == 0.0
