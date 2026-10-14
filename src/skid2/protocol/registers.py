"""Read generated simulator register banks using logical device telemetry."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import cast

from skid2.contracts import DataQuality
from skid2.protocol.codecs import CodecError, CodecSpec, encode_value, merge_bit
from skid2.sim.device import DeviceValue

ValueReader = Callable[[str], tuple[DeviceValue, ...]]


class RegisterError(ValueError):
    """A generated register request cannot be served safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RegisterDescription:
    """Safe read-only generated register instructions for simulator users."""

    logical_id: str
    function: int
    address: int
    display: str
    count: int
    unit: str


@dataclass(frozen=True, slots=True)
class _RegisterPoint:
    device_id: str
    logical_id: str
    function: int
    address: int
    display: str
    bit: int | None
    access: str
    unit: str
    codec: CodecSpec

    @property
    def end(self) -> int:
        return self.address + self.codec.word_count


class RegisterBank:
    """Translate generated register requests into encoded Modbus words."""

    def __init__(
        self,
        points: Mapping[str, tuple[_RegisterPoint, ...]],
        read_values: ValueReader,
    ) -> None:
        self._points = dict(points)
        self._read_values = read_values

    @classmethod
    def load(cls, path: Path, read_values: ValueReader) -> RegisterBank:
        """Load candidate points from the generated simulator register artifact."""

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RegisterError("invalid_bank", f"cannot load register bank: {path}") from exc
        document = _mapping(raw, "register bank")
        devices = _mapping(document.get("devices"), "register bank devices")
        result: dict[str, tuple[_RegisterPoint, ...]] = {}
        for device_id, raw_points in devices.items():
            points = tuple(
                point
                for item in _sequence(raw_points, f"registers for {device_id}")
                if (point := _parse_point(device_id, item)) is not None
            )
            if points:
                result[device_id] = points
        if not result:
            raise RegisterError("invalid_bank", "register bank contains no candidate points")
        return cls(result, read_values)

    @property
    def devices(self) -> tuple[str, ...]:
        """Return device routes in stable order."""

        return tuple(sorted(self._points))

    def addresses(self, device_id: str) -> tuple[tuple[int, int], ...]:
        """Return unique function/address pairs used to build network blocks."""

        points = self._device_points(device_id)
        return tuple(
            sorted(
                {
                    (point.function, address)
                    for point in points
                    for address in range(point.address, point.end)
                }
            )
        )

    def functions(self, device_id: str) -> tuple[int, ...]:
        """Return supported read functions for one generated device bank."""

        return tuple(sorted({point.function for point in self._device_points(device_id)}))

    def descriptions(self, device_id: str) -> tuple[RegisterDescription, ...]:
        """Return generated read instructions without exposing mutable internals."""

        return tuple(
            RegisterDescription(
                point.logical_id,
                point.function,
                point.address,
                point.display,
                point.codec.word_count,
                point.unit,
            )
            for point in sorted(
                self._device_points(device_id),
                key=lambda item: (item.address, item.bit if item.bit is not None else -1),
            )
        )

    def read(
        self,
        device_id: str,
        function: int,
        address: int,
        count: int,
    ) -> tuple[int, ...]:
        """Encode one complete, gap-free generated register request."""

        if count < 1:
            raise RegisterError("illegal_address", "register count must be positive")
        points = self._device_points(device_id)
        request_end = address + count
        selected = tuple(
            point
            for point in points
            if point.function == function and point.address < request_end and point.end > address
        )
        covered = {
            register
            for point in selected
            for register in range(max(address, point.address), min(request_end, point.end))
        }
        expected = set(range(address, request_end))
        if covered != expected:
            other_function = any(
                point.function != function and point.address < request_end and point.end > address
                for point in points
            )
            code = "wrong_function" if not selected and other_function else "illegal_address"
            raise RegisterError(code, "register request is not in the generated bank")

        telemetry = self._telemetry(device_id)
        words: list[int | None] = [None] * count
        bit_words: dict[int, int] = {}
        for point in selected:
            value = telemetry.get(point.logical_id)
            if value is None:
                raise RegisterError(
                    "missing_value",
                    f"simulator has no value for {point.logical_id}",
                )
            if value.quality is not DataQuality.GOOD:
                raise RegisterError(
                    "bad_quality",
                    f"simulator value is not good for {point.logical_id}",
                )
            if point.bit is not None:
                enabled = _bit_value(value, point.logical_id)
                current = bit_words.get(point.address, 0)
                try:
                    bit_words[point.address] = merge_bit(current, point.bit, enabled)
                except CodecError as exc:
                    raise RegisterError("encoding", str(exc)) from exc
                continue
            try:
                encoded = encode_value(_engineering_value(value), point.codec)
            except CodecError as exc:
                raise RegisterError("encoding", str(exc)) from exc
            for index, word in enumerate(encoded):
                register = point.address + index
                if address <= register < request_end:
                    words[register - address] = word

        for register, word in bit_words.items():
            if address <= register < request_end:
                words[register - address] = word
        if any(word is None for word in words):
            raise RegisterError("illegal_address", "register request crosses a bank hole")
        return tuple(cast(int, word) for word in words)

    def write(self, device_id: str, address: int, values: Sequence[int]) -> None:
        """Reject writes because every current generated point is read-only."""

        points = self._device_points(device_id)
        if not values:
            raise RegisterError("illegal_address", "register write must contain a value")
        request_end = address + len(values)
        if not any(point.address < request_end and point.end > address for point in points):
            raise RegisterError("illegal_address", "register is not in the generated bank")
        raise RegisterError("read_only", "generated simulator registers are read-only")

    def _device_points(self, device_id: str) -> tuple[_RegisterPoint, ...]:
        try:
            return self._points[device_id]
        except KeyError as exc:
            raise RegisterError("unknown_device", f"unknown register device: {device_id}") from exc

    def _telemetry(self, device_id: str) -> dict[str, DeviceValue]:
        values: dict[str, DeviceValue] = {}
        for item in self._read_values(device_id):
            if item.logical_id in values:
                raise RegisterError(
                    "duplicate_value",
                    f"simulator returned duplicate value {item.logical_id}",
                )
            values[item.logical_id] = item
        return values


def _parse_point(device_id: str, raw: object) -> _RegisterPoint | None:
    item = _mapping(raw, f"register point for {device_id}")
    if item.get("lifecycle") != "candidate":
        return None
    if item.get("device_id") != device_id:
        raise RegisterError("invalid_bank", f"register device identity differs for {device_id}")
    address = _mapping(item.get("address"), "register address")
    wire = _mapping(item.get("wire"), "register wire")
    engineering = _mapping(item.get("engineering"), "register engineering")
    service = _string(item.get("service"), "register service")
    function = {
        "modbus_holding_register_03_06": 3,
        "modbus_input_register_04": 4,
    }.get(service)
    if function is None:
        raise RegisterError("invalid_bank", f"unsupported generated service: {service}")
    bit_value = address.get("bit")
    bit = None if bit_value is None else _integer(bit_value, "register bit")
    sentinels = tuple(
        _integer(value, "register sentinel")
        for value in _sequence(engineering.get("sentinels"), "register sentinels")
    )
    return _RegisterPoint(
        device_id=device_id,
        logical_id=_string(item.get("logical_id"), "register logical id"),
        function=function,
        address=_integer(address.get("wire_offset"), "register wire offset"),
        display=_string(address.get("display"), "register display address"),
        bit=bit,
        access=_string(item.get("access"), "register access"),
        unit=_string(engineering.get("unit"), "register unit"),
        codec=CodecSpec(
            primitive=_string(wire.get("primitive"), "register primitive"),
            word_count=_integer(wire.get("word_count"), "register word count"),
            byte_order=_string(wire.get("byte_order"), "register byte order"),
            word_order=_string(wire.get("word_order"), "register word order"),
            scale=_decimal(engineering.get("scale"), "register scale"),
            offset=_decimal(engineering.get("offset"), "register offset"),
            minimum=_optional_decimal(engineering.get("minimum"), "register minimum"),
            maximum=_optional_decimal(engineering.get("maximum"), "register maximum"),
            sentinels=sentinels,
            rounding=_string(engineering.get("rounding"), "register rounding"),
            bit=bit,
        ),
    )


def _engineering_value(value: DeviceValue) -> str:
    if isinstance(value.value, bool):
        return "1" if value.value else "0"
    return str(value.value)


def _bit_value(value: DeviceValue, logical_id: str) -> bool:
    if isinstance(value.value, bool):
        return value.value
    if isinstance(value.value, int) and value.value in {0, 1}:
        return bool(value.value)
    raise RegisterError("encoding", f"bit value for {logical_id} must be boolean")


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise RegisterError("invalid_bank", f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise RegisterError("invalid_bank", f"{label} must be a list")
    return cast(Sequence[object], value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RegisterError("invalid_bank", f"{label} must be a non-empty string")
    return value


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RegisterError("invalid_bank", f"{label} must be an integer")
    return value


def _decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise RegisterError("invalid_bank", f"{label} must be numeric")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise RegisterError("invalid_bank", f"{label} must be numeric") from exc
    if not result.is_finite():
        raise RegisterError("invalid_bank", f"{label} must be finite")
    return result


def _optional_decimal(value: object, label: str) -> Decimal | None:
    return None if value is None else _decimal(value, label)
