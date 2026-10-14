"""Poll the selected battery connection and publish saved readings."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import timedelta
from typing import Any

from pymodbus.exceptions import ModbusException
from sqlalchemy.exc import SQLAlchemyError

from skid2.application.current_state import CurrentState
from skid2.application.ports import MeasurementStoreError
from skid2.contracts import DataQuality, Measurement, SystemClock
from skid2.persistence.config import ConfigRepository
from skid2.persistence.telemetry import TelemetryRepository
from skid2.protocol.client import DeviceReader, load_soc
from skid2.protocol.codecs import CodecError
from skid2.settings import Settings

LOGGER = logging.getLogger(__name__)


class DeviceManager:
    def __init__(
        self, settings: Settings, config: ConfigRepository, telemetry: TelemetryRepository
    ) -> None:
        self.settings = settings
        self.config = config
        self.telemetry = telemetry
        self.clock = SystemClock()
        self.state = CurrentState(self.clock, timedelta(seconds=3))
        self.revision = -1
        self.active: dict[str, Any] | None = None
        self.connection = "not_configured"
        self.message = "Save a device connection to start monitoring."
        self.reader: DeviceReader | None = None
        self.task: asyncio.Task[None] | None = None
        self.stop_event = asyncio.Event()
        self.changed = asyncio.Event()
        self.change_lock = asyncio.Lock()

    async def start(self) -> None:
        self.task = asyncio.create_task(self._run(), name="battery-monitor")

    async def stop(self) -> None:
        self.stop_event.set()
        self.changed.set()
        if self.task is not None:
            await self.task
        if self.reader is not None:
            self.reader.close()

    async def apply(self, saved: dict[str, Any]) -> None:
        async with self.change_lock:
            if saved["revision"] <= self.revision:
                return
            active = saved["connections"].get(saved["active"])
            reader = None
            if active is not None:
                live = active["mode"] == "live"
                path = (
                    self.settings.live_bess_map
                    if live
                    else self.settings.protocol_root / "devices/ba1-bess-v1.18.candidate.yaml"
                )
                if path is None:
                    raise ValueError(
                        "An approved battery register map is required for live equipment"
                    )
                point = load_soc(path, live=live)
                reader = DeviceReader(
                    active["host"],
                    active["port"],
                    active["timeout_seconds"],
                    point,
                    active["unit_id"],
                )
            # Prepare completely before replacing the installed reader or its revision.
            if self.reader is not None:
                self.reader.close()
            self.reader = reader
            self.active = active
            self.revision = saved["revision"]
            self.state = CurrentState(
                self.clock,
                timedelta(
                    seconds=3 if active is None else active["stale_seconds"],
                ),
            )
            self.connection = "not_configured" if active is None else "connecting"
            self.message = (
                "No connection selected."
                if active is None
                else "Connecting to the selected device."
            )

    async def poll(self) -> None:
        reader, revision, active = self.reader, self.revision, self.active
        if reader is None or active is None:
            return
        try:
            async with asyncio.timeout(active["timeout_seconds"]):
                decoded = await reader.read()
            async with self.change_lock:
                if revision != self.revision:
                    return
                if decoded.quality != "good" or decoded.value is None:
                    self.connection = "invalid"
                    self.message = "The device returned an invalid battery reading."
                    return
                now = self.clock.now()
                measurement = Measurement(
                    point_id="bess.cluster.soc",
                    label="Battery charge",
                    source=f"{active['mode']}:{active['device_id']}:revision:{revision}",
                    value=float(decoded.value),
                    unit="ratio",
                    source_timestamp=now,
                    receive_timestamp=now,
                    quality=DataQuality.GOOD,
                    synthetic=active["mode"] == "sim",
                )
                await asyncio.to_thread(self.telemetry.append, measurement)
                self.state.accept(measurement)
                self.connection = "connected"
                self.message = "Receiving saved battery readings."
        except (ConnectionError, TimeoutError, OSError, ModbusException):
            if revision == self.revision:
                reader.close()
                self.connection = "disconnected"
                self.message = "Device disconnected. The displayed zero is not a measured reading."
        except CodecError:
            if revision == self.revision:
                self.connection = "invalid"
                self.message = "The device returned an invalid register response."
        except MeasurementStoreError:
            if revision == self.revision:
                self.connection = "storage_error"
                self.message = "The reading could not be saved. Check the database connection."

    async def _run(self) -> None:
        while not self.stop_event.is_set():
            self.changed.clear()
            try:
                await self.apply(await asyncio.to_thread(self.config.load))
                await self.poll()
            except (ValueError, OSError):
                self.connection = "invalid"
                self.message = "Saved settings cannot be installed. Review the connection settings."
            except SQLAlchemyError:
                self.connection = "storage_error"
                self.message = "Saved connection settings are unavailable. Check the database."
                LOGGER.warning("Monitoring database unavailable")
            interval = 1.0 if self.active is None else self.active["poll_seconds"]
            with suppress(TimeoutError):
                await asyncio.wait_for(self.changed.wait(), timeout=interval)
