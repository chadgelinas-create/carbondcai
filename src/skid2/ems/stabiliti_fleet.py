"""ST-4/5/6 fleet helpers for EMS SIM. Units are ST-4/5/6, not Linear ST-1/2/3."""

from __future__ import annotations

from collections.abc import Callable

from skid2.ems.hub import EmsHub

UNITS: tuple[str, ...] = ("st-4", "st-5", "st-6")
LIMIT_KEYS: dict[str, str] = {
    "st-4": "st4_power_limit",
    "st-5": "st5_power_limit",
    "st-6": "st6_power_limit",
}
OPERATIONAL_LABELS: tuple[str, ...] = (
    "offline",
    "ready",
    "enabled",
    "derated",
    "faulted",
    "recovering",
)


def operational_label(code: float) -> str:
    index = int(code)
    if 0 <= index < len(OPERATIONAL_LABELS):
        return OPERATIONAL_LABELS[index]
    return "offline"


_last_ops: dict[str, str] = {}


def publish_state_events(hub: EmsHub, record_event: Callable[[str, str, str | None], None]) -> None:
    for unit in UNITS:
        point = hub.get(f"{unit}.operational.state")
        if point is None:
            continue
        label = "offline" if point.stale else operational_label(point.value)
        if _last_ops.get(unit) == label:
            continue
        _last_ops[unit] = label
        record_event("stabiliti", f"{unit} operational state {label}", unit)


def split_site_load(
    site_kw: float,
    limits: dict[str, float],
    online: dict[str, bool],
) -> dict[str, float]:
    remaining = max(0.0, site_kw)
    active = [unit for unit in UNITS if online.get(unit) and limits.get(unit, 0.0) > 0]
    allocated = dict.fromkeys(UNITS, 0.0)
    if not active or remaining <= 0:
        return allocated
    share = remaining / len(active)
    for unit in active:
        give = min(share, limits[unit], remaining)
        allocated[unit] = give
        remaining -= give
    return allocated
