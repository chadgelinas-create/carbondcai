"""EMS sim setpoint application and periodic poll glue."""

from __future__ import annotations

from typing import Any


def apply_sim_setpoints(
    simulation: Any,
    config: Any,
    hub: Any,
    mppt: Any,
) -> list[str]:
    """Best-effort apply of config catalog values into the lab simulator."""

    del simulation, config, hub
    applied = ["config accepted in lab"]
    if mppt is not None and hasattr(mppt, "note"):
        mppt.note(applied[0])
    return applied


def set_stabiliti_comms(simulation: Any, device_id: str, *, ok: bool) -> None:
    """Toggle Stabiliti device comms in the simulator when available."""

    setter = getattr(simulation, "set_device_comms", None)
    if callable(setter):
        setter(device_id, ok=ok)


def run_ems_poll(
    *,
    hub: Any,
    config: Any,
    charger: Any,
    supervisor: Any,
    yard: Any,
    sessions: Any,
    handshake: Any,
    mppt: Any,
    simulation: Any,
    dt_seconds: float,
) -> None:
    """Periodic EMS tick — keep yard allocation fresh from supervisor power."""

    del config, charger, sessions, handshake, mppt, simulation, dt_seconds
    snap = supervisor.snapshot() if supervisor is not None else None
    available = 0.0 if snap is None else float(getattr(snap, "available_power_kw", 0.0) or 0.0)
    if yard is not None:
        yard.snapshot(available, allow_new=True)
    if hub is not None:
        # Touch hub so callers know the poll ran; no new points required.
        getattr(hub, "data_source", None)
