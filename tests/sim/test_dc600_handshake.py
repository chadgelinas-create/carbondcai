"""Logical DC600 handshake phases without a vendor register map."""

from datetime import UTC, datetime, timedelta

from skid2.sim.dc600 import Dc600Inputs, Dc600Model, Dc600State
from skid2.sim.device import DeviceIdentity, DeviceStep, DeviceStepResult

NOW = datetime(2026, 10, 13, 12, tzinfo=UTC)


def _step(state: Dc600State, inputs: Dc600Inputs) -> DeviceStepResult[Dc600State]:
    model = Dc600Model(DeviceIdentity("dc600", "DC600"), ())
    return model.step(DeviceStep(state, inputs, (), None, NOW, timedelta(seconds=1)))


def test_handshake_requested_then_acked_then_charging() -> None:
    model = Dc600Model(DeviceIdentity("dc600", "DC600"), ())
    live = Dc600Inputs(
        demand_power=60,
        available_power=100,
        session_active=True,
        demand_fresh=True,
        available_fresh=True,
    )
    first = _step(model.initial_state(None), live)
    assert first.state.status == "Requested"
    second = _step(first.state, live)
    assert second.state.status == "Acknowledged"
    third = _step(second.state, live)
    assert third.state.status == "Charging"


def test_stale_values_after_charging_time_out() -> None:
    model = Dc600Model(DeviceIdentity("dc600", "DC600"), ())
    live = Dc600Inputs(
        demand_power=60,
        available_power=100,
        session_active=True,
        demand_fresh=True,
        available_fresh=True,
    )
    charging = _step(model.initial_state(None), live)
    charging = _step(charging.state, live)
    charging = _step(charging.state, live)
    stale = _step(
        charging.state,
        Dc600Inputs(
            demand_power=60,
            available_power=100,
            session_active=True,
            demand_fresh=False,
            available_fresh=True,
        ),
    )
    assert stale.state.status == "Timeout"
    recovered = _step(stale.state, live)
    assert recovered.state.status == "Recovering"


def test_limited_when_available_below_demand() -> None:
    model = Dc600Model(DeviceIdentity("dc600", "DC600"), ())
    live = Dc600Inputs(demand_power=200, available_power=80, session_active=True)
    first = _step(model.initial_state(None), live)
    assert first.state.status == "Limited"
    assert first.state.limited is True
