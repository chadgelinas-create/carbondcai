"""Simulator-only Modbus TCP network adapter."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from typing import Literal

from pymodbus.constants import ExcCodes
from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice

from skid2.protocol.registers import RegisterBank, RegisterDescription, RegisterError
from skid2.settings import Settings

GatewayState = Literal["ready", "stopped", "problem"]


@dataclass(frozen=True, slots=True)
class GatewayRoute:
    """One simulator device exposed through a Modbus device ID."""

    device_id: str
    modbus_id: int
    functions: tuple[int, ...]
    registers: tuple[RegisterDescription, ...]


@dataclass(frozen=True, slots=True)
class GatewayStatus:
    """User-safe snapshot of the simulator gateway."""

    state: GatewayState
    host: str
    port: int
    clients: int
    last_request: str | None
    routes: tuple[GatewayRoute, ...]


class ModbusGateway:
    """Own a real Modbus TCP listener backed by generated registers."""

    def __init__(self, settings: Settings, bank: RegisterBank) -> None:
        self._bind_host = settings.modbus_bind_host
        self._client_host = settings.modbus_client_host
        self._port = settings.modbus_port
        self._bank = bank
        configured = (
            ("ba1-1", settings.modbus_ba1_id),
            ("cnvtr-1", settings.modbus_cnvtr1_id),
            ("cnvtr-2", settings.modbus_cnvtr2_id),
        )
        self._routes = tuple(
            GatewayRoute(
                device_id,
                modbus_id,
                bank.functions(device_id),
                bank.descriptions(device_id),
            )
            for device_id, modbus_id in configured
        )
        self._device_by_modbus_id = {route.modbus_id: route.device_id for route in self._routes}
        self._state: GatewayState = "stopped"
        self._clients = 0
        self._last_request: str | None = None
        self._server: ModbusTcpServer | None = None

    async def start(self) -> None:
        """Bind the listener and begin accepting clients."""

        if self._server is not None:
            return
        try:
            devices = self._sim_devices()
            server = ModbusTcpServer(
                devices,
                address=(self._bind_host, self._port),
                trace_connect=self._connection_changed,
            )
            await server.serve_forever(background=True)
        except Exception:
            self._state = "problem"
            raise
        self._server = server
        self._state = "ready"

    async def stop(self) -> None:
        """Close clients and release the listener."""

        server = self._server
        self._server = None
        if server is not None:
            await server.shutdown()  # type: ignore[no-untyped-call]
        self._clients = 0
        self._state = "stopped"

    def status(self) -> GatewayStatus:
        """Return the latest listener and client state."""

        return GatewayStatus(
            state=self._state,
            host=self._client_host,
            port=self._port,
            clients=self._clients,
            last_request=self._last_request,
            routes=self._routes,
        )

    def _sim_devices(self) -> list[SimDevice]:
        devices = [
            SimDevice(
                id=0,
                simdata=SimData(
                    address=0,
                    count=65536,
                    values=0,
                    datatype=DataType.REGISTERS,
                    readonly=True,
                ),
                action=partial(self._access, None),
            )
        ]
        for route in self._routes:
            addresses = sorted({address for _, address in self._bank.addresses(route.device_id)})
            registers = [
                SimData(
                    address=address,
                    values=0,
                    datatype=DataType.REGISTERS,
                    readonly=True,
                )
                for address in addresses
            ]
            devices.append(
                SimDevice(
                    id=route.modbus_id,
                    simdata=registers,
                    action=partial(self._access, route.device_id),
                )
            )
        return devices

    async def _access(
        self,
        device_id: str | None,
        function: int,
        start_address: int,
        address: int,
        count: int,
        current_registers: list[int],
        set_values: list[int] | list[bool] | None,
    ) -> ExcCodes | None:
        self._last_request = (
            f"Device {self._modbus_id(device_id)} function {function} "
            f"address 0x{address:04X} count {count}"
        )
        if device_id is None:
            return ExcCodes.GATEWAY_NO_RESPONSE
        try:
            if set_values is None:
                words = self._bank.read(device_id, function, address, count)
                offset = address - start_address
                current_registers[offset : offset + count] = words
            else:
                self._bank.write(device_id, address, _register_values(set_values))
        except RegisterError as exc:
            return _exception_code(exc)
        return None

    def _modbus_id(self, device_id: str | None) -> int:
        if device_id is None:
            return 0
        return next(route.modbus_id for route in self._routes if route.device_id == device_id)

    def _connection_changed(self, connected: bool) -> None:
        if connected:
            self._clients += 1
        else:
            self._clients = max(0, self._clients - 1)


def stopped_gateway_status(settings: Settings) -> GatewayStatus:
    """Describe configured simulator routes while the listener is disabled."""

    return GatewayStatus(
        state="stopped",
        host=settings.modbus_client_host,
        port=settings.modbus_port,
        clients=0,
        last_request=None,
        routes=(
            GatewayRoute("ba1-1", settings.modbus_ba1_id, (4,), ()),
            GatewayRoute("cnvtr-1", settings.modbus_cnvtr1_id, (3,), ()),
            GatewayRoute("cnvtr-2", settings.modbus_cnvtr2_id, (3,), ()),
        ),
    )


def _register_values(values: Sequence[int | bool]) -> tuple[int, ...]:
    if any(isinstance(value, bool) for value in values):
        raise RegisterError("wrong_function", "coil writes are unsupported")
    return tuple(values)


def _exception_code(error: RegisterError) -> ExcCodes:
    if error.code == "wrong_function":
        return ExcCodes.ILLEGAL_FUNCTION
    if error.code in {"illegal_address", "read_only"}:
        return ExcCodes.ILLEGAL_ADDRESS
    if error.code == "unknown_device":
        return ExcCodes.GATEWAY_NO_RESPONSE
    return ExcCodes.DEVICE_FAILURE
