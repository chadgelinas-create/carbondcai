"""Apply EMS-owned converter/MPPT setpoints onto the in-process simulator."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from skid2.application.simulation import SimulationService
from skid2.ems.charger import ChargerAvailability
from skid2.ems.charger_handshake import HandshakeMachine
from skid2.ems.charger_sessions import ChargerSessionStore
from skid2.ems.charger_yard import ChargerYard
from skid2.ems.config import ConfigService
from skid2.ems.hub import EmsHub
from skid2.ems.mppt import MpptController
from skid2.ems.stabiliti_fleet import LIMIT_KEYS, UNITS, publish_state_events, split_site_load
from skid2.ems.supervisor import Supervisor
from skid2.sim.converter import ConverterInputs
from skid2.sim.dc600 import Dc600Inputs
from skid2.sim.stabiliti import StabilitiInputs


def run_ems_poll(
    *,
    hub: EmsHub,
    config: ConfigService,
    charger: ChargerAvailability,
    supervisor: Supervisor,
    yard: ChargerYard,
    sessions: ChargerSessionStore,
    handshake: HandshakeMachine,
    mppt: MpptController,
    simulation: SimulationService | None,
    dt_seconds: float,
) -> None:
    soc_point = hub.get("bess.cluster.soc")
    soc = 0.0 if soc_point is None or soc_point.stale else soc_point.value
    decision = charger.evaluate(soc)
    available = supervisor.advance_envelope(dt_seconds).available_kw
    snap = yard.tick(
        dt_seconds,
        available,
        allow_new=decision.allow_new_sessions,
        energy_cap_kwh=decision.energy_cap_kwh,
    )
    sessions.update_open(snap)
    occupied = any(port.occupied for port in snap.ports)
    allocated = sum(port.allocated_kw for port in snap.ports)
    handshake.step(
        occupied=occupied,
        demand_kw=snap.demand_kw,
        available_kw=available,
        communication_ok=hub.heartbeats.get("dc600", True),
        allocated_kw=allocated,
        fresh=True,
    )
    publish_state_events(hub, supervisor.record_event)
    if simulation is None:
        return
    apply_sim_setpoints(
        simulation,
        config,
        hub,
        mppt,
        charger_demand_kw=snap.demand_kw,
        charger_available_kw=available,
        charger_actual_kw=allocated,
        session_active=occupied,
    )


def apply_sim_setpoints(
    simulation: SimulationService,
    config: ConfigService,
    hub: EmsHub,
    mppt: MpptController,
    *,
    charger_demand_kw: float | None = None,
    charger_available_kw: float | None = None,
    charger_actual_kw: float | None = None,
    session_active: bool = False,
) -> list[str]:
    """Best-effort write-back. Fault-test inputs are left untouched."""

    notes: list[str] = []
    soc_point = hub.get("bess.cluster.soc")
    soc = 0.5 if soc_point is None or soc_point.stale else soc_point.value
    cnvtr1_limit = config.numeric("cnvtr", "cnvtr1_power_limit")
    cnvtr2_limit = config.numeric("cnvtr", "cnvtr2_power_limit")
    decision = mppt.evaluate(soc, cnvtr2_limit, config)
    notes.extend(
        _patch_converter(
            simulation,
            "cnvtr-1",
            lambda current: replace(
                current,
                available_power=min(current.available_power or cnvtr1_limit, cnvtr1_limit),
                discharge_power_limit=min(current.discharge_power_limit, cnvtr1_limit),
                charge_power_limit=min(current.charge_power_limit, cnvtr1_limit),
            ),
        )
    )
    notes.extend(
        _patch_converter(
            simulation,
            "cnvtr-2",
            lambda current: replace(
                current,
                enabled=decision.enabled and current.enabled,
                requested_power=min(decision.requested_kw, cnvtr2_limit),
                available_power=min(current.available_power or cnvtr2_limit, cnvtr2_limit),
                mppt_applying=decision.applying,
            ),
        )
    )
    if charger_demand_kw is not None:
        notes.extend(
            _patch_dc600(
                simulation,
                charger_demand_kw,
                charger_available_kw if charger_available_kw is not None else 0.0,
                charger_actual_kw,
                session_active,
            )
        )
    try:
        notes.extend(_patch_stabiliti_fleet(simulation, config, hub))
    except (KeyError, ValueError):
        pass
    return notes


def _patch_converter(
    simulation: SimulationService,
    device_id: str,
    mutate: Callable[[ConverterInputs], ConverterInputs],
) -> list[str]:
    current = _inputs(simulation, device_id)
    if not isinstance(current, ConverterInputs):
        return []
    if (
        not current.communication_ok
        or current.fault_active
        or current.test_derate_limit is not None
    ):
        return []
    updated = mutate(current)
    if updated == current:
        return []
    try:
        simulation.set_inputs(device_id, updated)
    except ValueError:
        return []
    return [f"{device_id} setpoints applied"]


def set_stabiliti_comms(simulation: SimulationService, device_id: str, *, ok: bool) -> None:
    if device_id not in UNITS:
        return
    current = _inputs(simulation, device_id)
    if not isinstance(current, StabilitiInputs) or current.communication_ok is ok:
        return
    try:
        simulation.set_inputs(device_id, replace(current, communication_ok=ok))
    except ValueError:
        return


def _patch_stabiliti_fleet(
    simulation: SimulationService, config: ConfigService, hub: EmsHub
) -> list[str]:
    limits = {unit: config.numeric("stabiliti", LIMIT_KEYS[unit]) for unit in UNITS}
    site = config.numeric("stabiliti", "site_ac_load")
    online: dict[str, bool] = {}
    current: dict[str, StabilitiInputs] = {}
    for unit in UNITS:
        inputs = _inputs(simulation, unit)
        if not isinstance(inputs, StabilitiInputs):
            continue
        current[unit] = inputs
        online[unit] = (
            inputs.enabled
            and inputs.communication_ok
            and not inputs.fault_active
            and hub.heartbeats.get(unit, True)
        )
    loads = split_site_load(site, limits, online)
    notes: list[str] = []
    for unit, inputs in current.items():
        if (
            not inputs.communication_ok
            or inputs.fault_active
            or inputs.derated
            or not hub.heartbeats.get(unit, True)
        ):
            continue
        updated = replace(
            inputs,
            power_limit=limits[unit],
            available_power=max(inputs.available_power, limits[unit]),
        )
        if site > 0:
            updated = replace(updated, bus_load=loads[unit], actual_power=loads[unit])
        if updated == inputs:
            continue
        try:
            simulation.set_inputs(unit, updated)
        except ValueError:
            continue
        notes.append(f"{unit} setpoints applied")
    return notes


def _patch_dc600(
    simulation: SimulationService,
    demand_kw: float,
    available_kw: float,
    actual_kw: float | None,
    session_active: bool,
) -> list[str]:
    current = _inputs(simulation, "dc600")
    if not isinstance(current, Dc600Inputs):
        return []
    if not current.communication_ok or current.fault_active:
        return []
    updated = replace(
        current,
        demand_power=demand_kw,
        available_power=available_kw,
        actual_power=actual_kw,
        session_active=session_active,
        demand_fresh=True,
        available_fresh=True,
    )
    if updated == current:
        return []
    try:
        simulation.set_inputs("dc600", updated)
    except ValueError:
        return []
    return ["dc600 handshake demand applied"]


def _inputs(simulation: SimulationService, device_id: str) -> object | None:
    try:
        devices = simulation.state().devices
    except (RuntimeError, ValueError):
        return None
    for item in devices:
        if item.device_id == device_id:
            return item.applied_inputs
    return None
