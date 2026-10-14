"""Shared framework-neutral domain contracts."""

from skid2.contracts.clock import Clock, ManualClock, SystemClock
from skid2.contracts.domain import (
    Alarm,
    AlarmSeverity,
    Command,
    ComponentStatus,
    DataQuality,
    Event,
    Freshness,
    Heartbeat,
    Measurement,
    SystemSnapshot,
)

__all__ = [
    "Alarm",
    "AlarmSeverity",
    "Clock",
    "Command",
    "ComponentStatus",
    "DataQuality",
    "Event",
    "Freshness",
    "Heartbeat",
    "ManualClock",
    "Measurement",
    "SystemClock",
    "SystemSnapshot",
]
