"""Read mapped battery registers through Modbus TCP."""

from __future__ import annotations

from pathlib import Path

from pymodbus.client import AsyncModbusTcpClient

from skid2.protocol.codecs import DecodedValue, codec_spec, decode_words
from skid2.protocol.loader import parse_document
from skid2.protocol.models import PointSpec
from skid2.protocol.validation import validate_document


class DeviceReadError(ConnectionError):
    """The device could not provide a register response."""


def load_soc(path: Path, *, live: bool) -> PointSpec:
    document = parse_document(path)
    report = validate_document(document, "production" if live else "candidate")
    if not report.accepted:
        raise ValueError("Battery register map is not approved for this data source")
    point = next((item for item in document.points if item.logical_id == "bess.cluster.soc"), None)
    if point is None or point.service != "modbus_input_register_04":
        raise ValueError("Battery charge input register is missing")
    if point.engineering.unit != "ratio":
        raise ValueError("Battery charge must use the declared ratio unit")
    return point


class DeviceReader:
    def __init__(
        self, host: str, port: int, timeout: float, point: PointSpec, unit_id: int
    ) -> None:
        self.client = AsyncModbusTcpClient(host, port=port, timeout=timeout, retries=0)
        self.point = point
        self.unit_id = unit_id

    async def read(self) -> DecodedValue:
        if not self.client.connected and not await self.client.connect():
            raise DeviceReadError("Device connection failed")
        response = await self.client.read_input_registers(
            self.point.address.wire_offset, count=self.point.wire.word_count, device_id=self.unit_id
        )
        if response.isError():
            raise DeviceReadError("Battery register read failed")
        return decode_words(tuple(response.registers), codec_spec(self.point))

    def close(self) -> None:
        self.client.close()
