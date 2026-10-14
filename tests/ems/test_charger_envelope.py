"""PMS → EVCS aggregate Available-Power envelope (Oct 2026 clarification)."""

from skid2.ems.charger_envelope import BUS_CAP_KW, ChargerEnvelope
from skid2.ems.charger_yard import ChargerYard


def test_healthy_bess_caps_at_600() -> None:
    env = ChargerEnvelope()
    decision = env.compute(
        pv_kw=216.0,
        soc=0.5,
        bess_limit_kw=600.0,
        bus_v=750.0,
        comms_ok=True,
        charger_fault=False,
    )
    assert decision.status == 0
    assert decision.target_kw == BUS_CAP_KW
    assert decision.available_kw == BUS_CAP_KW


def test_bess_reserve_is_pv_only() -> None:
    env = ChargerEnvelope()
    decision = env.compute(
        pv_kw=80.0,
        soc=0.07,
        bess_limit_kw=400.0,
        bus_v=750.0,
        comms_ok=True,
        charger_fault=False,
    )
    assert decision.status == 4
    assert decision.available_kw == 80.0
    assert decision.bess_kw == 0.0


def test_fault_zeros_available_then_holds() -> None:
    env = ChargerEnvelope()
    lost = env.compute(
        pv_kw=216.0,
        soc=0.5,
        bess_limit_kw=600.0,
        bus_v=750.0,
        comms_ok=False,
        charger_fault=False,
        dt_seconds=1.0,
    )
    assert lost.status == 5
    assert lost.available_kw == 0.0
    held = env.compute(
        pv_kw=216.0,
        soc=0.5,
        bess_limit_kw=600.0,
        bus_v=750.0,
        comms_ok=True,
        charger_fault=False,
        dt_seconds=1.0,
    )
    assert held.status == 5
    assert held.available_kw == 0.0


def test_port1_then_port3_share_600_envelope() -> None:
    yard = ChargerYard()
    yard.set_port("1", occupied=True, requested_kw=350.0, allow_new=True)
    first = yard.snapshot(600.0, allow_new=True)
    by_id = {port.port_id: port for port in first.ports}
    assert by_id["1"].allocated_kw == 350.0
    yard.set_port("3", occupied=True, requested_kw=300.0, allow_new=True)
    both = yard.snapshot(600.0, allow_new=True)
    by_id = {port.port_id: port for port in both.ports}
    assert both.demand_kw == 650.0
    assert by_id["1"].allocated_kw == 300.0
    assert by_id["3"].allocated_kw == 300.0
    assert both.unmet_kw == 50.0
