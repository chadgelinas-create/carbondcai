"""In-memory live telemetry fan-out for EMS WebSocket clients."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class LivePoint:
    point_id: str
    device_id: str
    group_id: str
    label: str
    value: float
    unit: str
    quality: str
    source_timestamp: datetime
    stale: bool


class EmsHub:
    """Hold latest points and broadcast change batches to subscribers."""

    def __init__(self) -> None:
        self._latest: dict[str, LivePoint] = {}
        self._queues: list[asyncio.Queue[dict[str, Any]]] = []
        self._lock = asyncio.Lock()
        self.data_source: str = "sim"
        self.heartbeats: dict[str, bool] = {}

    def snapshot(self, group_id: str | None = None) -> list[LivePoint]:
        points = list(self._latest.values())
        if group_id is None:
            return points
        return [point for point in points if point.group_id == group_id]

    def get(self, point_id: str) -> LivePoint | None:
        return self._latest.get(point_id)

    async def publish(self, points: list[LivePoint]) -> None:
        if not points:
            return
        payload = {
            "type": "telemetry",
            "data_source": self.data_source,
            "points": [_point_dict(point) for point in points],
        }
        async with self._lock:
            for point in points:
                self._latest[point.point_id] = point
            queues = list(self._queues)
        for queue in queues:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                continue

    async def publish_event(self, event: dict[str, Any]) -> None:
        payload = {"type": "event", "data_source": self.data_source, **event}
        async with self._lock:
            queues = list(self._queues)
        for queue in queues:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                continue

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        async with self._lock:
            self._queues.append(queue)
            bootstrap = {
                "type": "snapshot",
                "data_source": self.data_source,
                "points": [_point_dict(point) for point in self._latest.values()],
                "heartbeats": dict(self.heartbeats),
            }
        yield bootstrap
        try:
            while True:
                yield await queue.get()
        finally:
            async with self._lock:
                if queue in self._queues:
                    self._queues.remove(queue)


def _point_dict(point: LivePoint) -> dict[str, Any]:
    return {
        "point_id": point.point_id,
        "device_id": point.device_id,
        "group_id": point.group_id,
        "label": point.label,
        "value": 0.0 if point.stale else point.value,
        "unit": point.unit,
        "quality": point.quality,
        "source_timestamp": point.source_timestamp.isoformat(),
        "stale": point.stale,
        "display_value": 0.0 if point.stale else point.value,
    }
