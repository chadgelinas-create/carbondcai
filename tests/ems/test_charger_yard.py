"""Charger-as-master six-port field simulation."""

from __future__ import annotations

import pytest

from skid2.ems.charger_yard import ChargerYard


def test_port_two_then_port_five_share_available_power() -> None:
    yard = ChargerYard()
    yard.set_port("2", occupied=True, requested_kw=200.0, allow_new=True)
    yard.set_port("5", occupied=True, requested_kw=100.0, allow_new=True)
    snap = yard.snapshot(250.0, allow_new=True)
    by_id = {port.port_id: port for port in snap.ports}
    assert snap.demand_kw == 300.0
    assert by_id["2"].allocated_kw == 150.0
    assert by_id["5"].allocated_kw == 100.0
    assert snap.unmet_kw == 50.0


def test_blocked_soc_rejects_new_car() -> None:
    yard = ChargerYard()
    with pytest.raises(ValueError, match="blocked"):
        yard.set_port("2", occupied=True, requested_kw=200.0, allow_new=False)


def test_request_outside_30_200_rejected() -> None:
    yard = ChargerYard()
    with pytest.raises(ValueError, match="30"):
        yard.set_port("1", occupied=True, requested_kw=10.0, allow_new=True)


def test_unplug_clears_demand() -> None:
    yard = ChargerYard()
    yard.set_port("2", occupied=True, requested_kw=200.0, allow_new=True)
    yard.set_port("2", occupied=False, requested_kw=0.0, allow_new=True)
    snap = yard.snapshot(400.0, allow_new=True)
    assert snap.demand_kw == 0.0
    assert snap.ports[1].occupied is False


def test_unknown_port_rejected() -> None:
    yard = ChargerYard()
    with pytest.raises(KeyError):
        yard.set_port("9", occupied=True, requested_kw=60.0, allow_new=True)
