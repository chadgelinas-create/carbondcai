"""Poll simulator/device sources into historian + live EMS hub."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from skid2.application.ports import MeasurementStore
from skid2.contracts import DataQuality, Measurement
from skid2.ems.charger_yard import ChargerYard
from skid2.ems.groups import DEVICE_IDS, GROUP_DEVICES
from skid2.ems.hub import EmsHub, LivePoint
from skid2.sim.device import DeviceValue

LOGGER = logging.getLogger(__name__)

SimulationReader = Callable[[str], tuple[DeviceValue, ...]]


class DeviceManager:
    """Device Manager: decode engineering values and publish live EMS state.

    SIM mode reads the in-process simulator (fed by the same models exposed on
    Modbus). LIVE mode uses the same point contract against remote Modbus hosts
    later — the UI only sees data_source flipping.
    """

    def __init__(
        self,
        hub: EmsHub,
        repository: MeasurementStore | None,
        *,
        data_source: str = "sim",
        poll_seconds: float = 1.0,
    ) -> None:
        self._hub = hub
        self._repository = repository
        self._data_source = data_source
        self._poll_seconds = poll_seconds
        self._reader: SimulationReader | None = None
        self._connected = True
        self._force_zero_devices: set[str] = set()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._yard: ChargerYard | None = None
        self._poll_hook: Callable[[], None] | None = None
        self._hub.data_source = data_source

    def attach_yard(self, yard: ChargerYard) -> None:
        self._yard = yard

    def attach_poll_hook(self, hook: Callable[[], None]) -> None:
        self._poll_hook = hook

    @property
    def data_source(self) -> str:
        return self._data_source

    def set_data_source(self, value: str) -> None:
        if value not in {"sim", "live"}:
            raise ValueError("data_source must be sim or live")
        self._data_source = value
        self._hub.data_source = value

    def attach_simulation(self, reader: SimulationReader) -> None:
        self._reader = reader

    def set_connected(self, connected: bool) -> None:
        """Test/demo hook: simulate total source loss."""

        self._connected = connected

    def inject_comms_loss(self, device_id: str) -> None:
        self._force_zero_devices.add(device_id)

    def clear_comms_loss(self, device_id: str) -> None:
        self._force_zero_devices.discard(device_id)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="skid2-device-manager")

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None:
            await task

    async def poll_once(self) -> list[LivePoint]:
        if self._poll_hook is not None:
            try:
                self._poll_hook()
            except Exception:
                LOGGER.exception("ems poll hook failed")
        now = datetime.now(tz=UTC)
        published: list[LivePoint] = []
        heartbeats: dict[str, bool] = {}
        for device_id in DEVICE_IDS:
            group_id = _group_for(device_id)
            lost = (
                not self._connected
                or device_id in self._force_zero_devices
                or (self._data_source == "live")
            )
            values: tuple[DeviceValue, ...]
            if lost and self._data_source == "live":
                # LIVE without PLC adapters yet: honest zero/stale until PLC phase.
                values = ()
                quality = DataQuality.COMM_ERROR
                heartbeats[device_id] = False
            elif lost:
                values = ()
                quality = DataQuality.COMM_ERROR
                heartbeats[device_id] = False
            else:
                values = self._read_device(device_id)
                quality = DataQuality.GOOD
                heartbeats[device_id] = True
            if not values and (lost or self._reader is not None):
                # Emit known zeros for the group so UI shows 0 / stale.
                for point_id, unit in _zero_points(device_id):
                    live = LivePoint(
                        point_id=point_id,
                        device_id=device_id,
                        group_id=group_id,
                        label=point_id,
                        value=0.0,
                        unit=unit,
                        quality=quality.value,
                        source_timestamp=now,
                        stale=True,
                    )
                    published.append(live)
                    await self._persist(live, synthetic=self._data_source == "sim")
                continue
            for item in values:
                stale = item.quality is not DataQuality.GOOD
                numeric = _as_float(item.value)
                live = LivePoint(
                    point_id=item.logical_id,
                    device_id=device_id,
                    group_id=_point_group(device_id, item.logical_id),
                    label=item.logical_id,
                    value=0.0 if stale else numeric,
                    unit=item.unit,
                    quality=item.quality.value,
                    source_timestamp=now,
                    stale=stale,
                )
                published.append(live)
                await self._persist(live, synthetic=self._data_source == "sim")
        if self._yard is not None and "dc600" not in self._force_zero_devices and self._data_source != "live":
            published.extend(self._yard_points(now))
        self._hub.heartbeats = heartbeats
        await self._hub.publish(published)
        return published

    def _yard_points(self, now: datetime) -> list[LivePoint]:
        assert self._yard is not None
        snap = self._yard.snapshot(0.0, allow_new=True)
        vehicles = sum(1 for port in snap.ports if port.occupied)
        points = [
            LivePoint(
                "dc600.demand.power",
                "dc600",
                "charger",
                "Charger demand (sum of plugged ports)",
                snap.demand_kw,
                "kW",
                DataQuality.GOOD.value,
                now,
                False,
            ),
            LivePoint(
                "dc600.vehicles",
                "dc600",
                "charger",
                "Connected vehicles",
                float(vehicles),
                "count",
                DataQuality.GOOD.value,
                now,
                False,
            ),
            LivePoint(
                "dc600.evcs.demand.power",
                "dc600",
                "charger",
                "40001 EV Charger Power Demand",
                snap.demand_kw * 1000.0,
                "W",
                DataQuality.GOOD.value,
                now,
                False,
            ),
            LivePoint(
                "dc600.evcs.status",
                "dc600",
                "charger",
                "40003 EV Charger Status",
                1.0 if vehicles else 0.0,
                "enum",
                DataQuality.GOOD.value,
                now,
                False,
            ),
            LivePoint(
                "dc600.evcs.vehicles",
                "dc600",
                "charger",
                "40004 Connected Vehicles",
                float(vehicles),
                "count",
                DataQuality.GOOD.value,
                now,
                False,
            ),
        ]
        for port in snap.ports:
            points.append(
                LivePoint(
                    f"dc600.port.{port.port_id}.requested",
                    "dc600",
                    "charger",
                    f"Port {port.port_id} {port.label} request",
                    port.requested_kw,
                    "kW",
                    DataQuality.GOOD.value,
                    now,
                    False,
                )
            )
            points.append(
                LivePoint(
                    f"dc600.port.{port.port_id}.occupied",
                    "dc600",
                    "charger",
                    f"Port {port.port_id} occupied",
                    1.0 if port.occupied else 0.0,
                    "boolean",
                    DataQuality.GOOD.value,
                    now,
                    False,
                )
            )
            points.append(
                LivePoint(
                    f"dc600.port.{port.port_id}.allocated",
                    "dc600",
                    "charger",
                    f"Port {port.port_id} allocated",
                    port.allocated_kw,
                    "kW",
                    DataQuality.GOOD.value,
                    now,
                    False,
                )
            )
        return points

    def _read_device(self, device_id: str) -> tuple[DeviceValue, ...]:
        if self._reader is None:
            return ()
        try:
            return self._reader(device_id)
        except Exception:
            LOGGER.exception("device read failed for %s", device_id)
            return ()

    async def _persist(self, point: LivePoint, *, synthetic: bool) -> None:
        if self._repository is None:
            return
        measurement = Measurement(
            point_id=point.point_id,
            label=point.label,
            source=f"ems.{self._data_source}.{point.device_id}",
            value=point.value,
            unit=point.unit,
            source_timestamp=point.source_timestamp,
            receive_timestamp=datetime.now(tz=UTC),
            quality=DataQuality(point.quality)
            if point.quality in DataQuality._value2member_map_
            else DataQuality.INVALID,
            synthetic=synthetic,
        )
        try:
            await asyncio.to_thread(self._repository.append, measurement)
        except Exception:
            LOGGER.exception("historian append failed for %s", point.point_id)

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.poll_once()
            except Exception:
                LOGGER.exception("device manager poll failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_seconds)
            except TimeoutError:
                continue


def _group_for(device_id: str) -> str:
    for group_id, devices in GROUP_DEVICES.items():
        if group_id == "supervisor":
            continue
        if device_id in devices:
            if group_id == "solar":
                continue
            return group_id
    return "supervisor"


def _zero_points(device_id: str) -> tuple[tuple[str, str], ...]:
    defaults: dict[str, tuple[tuple[str, str], ...]] = {
        "ba1-1": (
            ("bess.cluster.soc", "ratio"),
            ("bess.cluster.voltage", "V"),
            ("bess.cluster.current", "A"),
            ("bess.cluster.temperature.maximum", "degC"),
            ("bess.cluster.limit.charge.power", "kW"),
            ("bess.cluster.limit.discharge.power", "kW"),
            ("bess.cluster.contactor.main", "boolean"),
            ("bess.cluster.contactor.precharge", "boolean"),
        ),
        "cnvtr-1": (("cnvtr-1.dc.power", "kW"),),
        "cnvtr-2": (("cnvtr-2.dc.power", "kW"),),
        "dc600": (
            ("dc600.demand.power", "kW"),
            ("dc600.available.power", "kW"),
            ("dc600.vehicles", "count"),
        ),
        "st-4": _stabiliti_zeros("st-4"),
        "st-5": _stabiliti_zeros("st-5"),
        "st-6": _stabiliti_zeros("st-6"),
    }
    return defaults.get(device_id, ((f"{device_id}.value", "1"),))


def _stabiliti_zeros(device_id: str) -> tuple[tuple[str, str], ...]:
    return (
        (f"{device_id}.ready", "boolean"),
        (f"{device_id}.enabled", "boolean"),
        (f"{device_id}.available", "boolean"),
        (f"{device_id}.derated", "boolean"),
        (f"{device_id}.operational.state", "enum"),
        (f"{device_id}.available.power", "kW"),
        (f"{device_id}.actual.power", "kW"),
        (f"{device_id}.ac.port.power", "kW"),
        (f"{device_id}.ac.port.volts", "V"),
        (f"{device_id}.ac.port.current", "A"),
        (f"{device_id}.dc.in.power", "kW"),
        (f"{device_id}.power.limit", "kW"),
        (f"{device_id}.temperature.maximum", "degC"),
        (f"{device_id}.communication.ok", "boolean"),
        (f"{device_id}.fault.active", "boolean"),
        (f"{device_id}.bus.load", "kW"),
    )


def _point_group(device_id: str, logical_id: str) -> str:
    if device_id == "cnvtr-2" and (
        logical_id.startswith("cnvtr2") or "mppt" in logical_id or "pv" in logical_id
    ):
        return "solar"
    return _group_for(device_id)


def _as_float(value: bool | int | float | str) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except ValueError:
        return 0.0
