"""Deterministic loading of JSON-compatible YAML protocol documents."""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import NoReturn, TypeVar, cast

from skid2.protocol.models import (
    AddressSpec,
    Applicability,
    BlockerSpec,
    CommandSpec,
    DeviceSpec,
    EngineeringSpec,
    JsonObject,
    JsonValue,
    PointProvenance,
    PointSpec,
    ProtocolDocument,
    QualitySpec,
    ReviewRecord,
    SourceReference,
    TimingSpec,
    WireSpec,
)

T = TypeVar("T")


class ProtocolInputError(ValueError):
    """An ordinary, location-aware protocol input error."""

    def __init__(self, source: Path, path: str, message: str) -> None:
        super().__init__(message)
        self.source = source
        self.path = path
        self.message = message


def _duplicate_guard(pairs: list[tuple[str, JsonValue]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def load_raw(path: Path) -> JsonObject:
    """Load the deterministic YAML subset and reject duplicate keys."""

    try:
        loaded = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_duplicate_guard,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProtocolInputError(path, "$", f"cannot parse JSON-compatible YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ProtocolInputError(path, "$", "document root must be an object")
    return cast(JsonObject, loaded)


class _Reader:
    def __init__(self, source: Path) -> None:
        self.source = source

    def fail(self, path: str, message: str) -> NoReturn:
        raise ProtocolInputError(self.source, path, message)

    def obj(self, value: JsonValue, path: str) -> JsonObject:
        if not isinstance(value, dict):
            self.fail(path, "must be an object")
        return value

    def array(self, value: JsonValue, path: str) -> list[JsonValue]:
        if not isinstance(value, list):
            self.fail(path, "must be an array")
        return value

    def string(self, value: JsonValue, path: str, *, allow_empty: bool = False) -> str:
        if not isinstance(value, str) or (not allow_empty and not value.strip()):
            self.fail(path, "must be a non-empty string")
        return value

    def integer(self, value: JsonValue, path: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            self.fail(path, "must be an integer")
        return value

    def boolean(self, value: JsonValue, path: str) -> bool:
        if not isinstance(value, bool):
            self.fail(path, "must be a boolean")
        return value

    def optional_integer(self, value: JsonValue, path: str) -> int | None:
        if value is None:
            return None
        return self.integer(value, path)

    def decimal(self, value: JsonValue, path: str) -> Decimal:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            self.fail(path, "must be a decimal string or number")
        try:
            result = Decimal(str(value))
        except InvalidOperation as exc:
            raise ProtocolInputError(self.source, path, "must be a finite decimal") from exc
        if not result.is_finite():
            self.fail(path, "must be a finite decimal")
        return result

    def optional_decimal(self, value: JsonValue, path: str) -> Decimal | None:
        if value is None:
            return None
        return self.decimal(value, path)

    def strings(self, value: JsonValue, path: str) -> tuple[str, ...]:
        return tuple(
            self.string(item, f"{path}[{index}]")
            for index, item in enumerate(self.array(value, path))
        )

    def required(self, obj: JsonObject, key: str, path: str) -> JsonValue:
        if key not in obj:
            self.fail(f"{path}.{key}", "is required")
        return obj[key]

    def items(
        self,
        value: JsonValue,
        path: str,
        parser: Callable[[JsonObject, str], T],
    ) -> tuple[T, ...]:
        parsed: list[T] = []
        for index, item in enumerate(self.array(value, path)):
            item_path = f"{path}[{index}]"
            parsed.append(parser(self.obj(item, item_path), item_path))
        return tuple(parsed)


def parse_document(path: Path) -> ProtocolDocument:
    """Parse one document into immutable contract values."""

    raw = load_raw(path)
    read = _Reader(path)

    def applicability(obj: JsonObject, base: str) -> Applicability:
        return Applicability(
            status=read.string(read.required(obj, "status", base), f"{base}.status"),
            values=read.strings(read.required(obj, "values", base), f"{base}.values"),
            evidence=read.string(read.required(obj, "evidence", base), f"{base}.evidence"),
        )

    def source(obj: JsonObject, base: str) -> SourceReference:
        return SourceReference(
            source_id=read.string(read.required(obj, "source_id", base), f"{base}.source_id"),
            filename=read.string(read.required(obj, "filename", base), f"{base}.filename"),
            revision=read.string(read.required(obj, "revision", base), f"{base}.revision"),
            sha256=read.string(read.required(obj, "sha256", base), f"{base}.sha256"),
            classification=read.string(
                read.required(obj, "classification", base), f"{base}.classification"
            ),
        )

    def review(obj: JsonObject, base: str) -> ReviewRecord:
        return ReviewRecord(
            authority=read.string(read.required(obj, "authority", base), f"{base}.authority"),
            reviewer=read.string(
                read.required(obj, "reviewer", base), f"{base}.reviewer", allow_empty=True
            ),
            status=read.string(read.required(obj, "status", base), f"{base}.status"),
            date=read.string(read.required(obj, "date", base), f"{base}.date", allow_empty=True),
            evidence=read.string(read.required(obj, "evidence", base), f"{base}.evidence"),
        )

    def address(obj: JsonObject, base: str) -> AddressSpec:
        bit_value = obj.get("bit")
        return AddressSpec(
            status=read.string(read.required(obj, "status", base), f"{base}.status"),
            display=read.string(read.required(obj, "display", base), f"{base}.display"),
            notation=read.string(read.required(obj, "notation", base), f"{base}.notation"),
            convention=read.string(read.required(obj, "convention", base), f"{base}.convention"),
            space=read.string(read.required(obj, "space", base), f"{base}.space"),
            wire_offset=read.integer(
                read.required(obj, "wire_offset", base), f"{base}.wire_offset"
            ),
            bit=read.optional_integer(bit_value, f"{base}.bit"),
        )

    def wire(obj: JsonObject, base: str) -> WireSpec:
        bits: list[tuple[int, str]] = []
        for bit_key, label_value in sorted(
            read.obj(read.required(obj, "bit_definitions", base), f"{base}.bit_definitions").items()
        ):
            try:
                bit_index = int(bit_key)
            except ValueError as exc:
                raise ProtocolInputError(
                    path, f"{base}.bit_definitions.{bit_key}", "bit key must be an integer"
                ) from exc
            bits.append(
                (
                    bit_index,
                    read.string(label_value, f"{base}.bit_definitions.{bit_key}"),
                )
            )
        return WireSpec(
            status=read.string(read.required(obj, "status", base), f"{base}.status"),
            primitive=read.string(read.required(obj, "primitive", base), f"{base}.primitive"),
            signed=read.boolean(read.required(obj, "signed", base), f"{base}.signed"),
            word_count=read.integer(read.required(obj, "word_count", base), f"{base}.word_count"),
            byte_order=read.string(read.required(obj, "byte_order", base), f"{base}.byte_order"),
            word_order=read.string(read.required(obj, "word_order", base), f"{base}.word_order"),
            atomic_group=read.string(
                read.required(obj, "atomic_group", base),
                f"{base}.atomic_group",
                allow_empty=True,
            ),
            bit_definitions=tuple(bits),
        )

    def engineering(obj: JsonObject, base: str) -> EngineeringSpec:
        sentinels = tuple(
            read.integer(item, f"{base}.sentinels[{index}]")
            for index, item in enumerate(
                read.array(read.required(obj, "sentinels", base), f"{base}.sentinels")
            )
        )
        return EngineeringSpec(
            status=read.string(read.required(obj, "status", base), f"{base}.status"),
            scale=read.decimal(read.required(obj, "scale", base), f"{base}.scale"),
            offset=read.decimal(read.required(obj, "offset", base), f"{base}.offset"),
            unit=read.string(read.required(obj, "unit", base), f"{base}.unit"),
            minimum=read.optional_decimal(obj.get("minimum"), f"{base}.minimum"),
            maximum=read.optional_decimal(obj.get("maximum"), f"{base}.maximum"),
            sentinels=sentinels,
            rounding=read.string(read.required(obj, "rounding", base), f"{base}.rounding"),
        )

    def timing(obj: JsonObject, base: str) -> TimingSpec:
        return TimingSpec(
            status=read.string(read.required(obj, "status", base), f"{base}.status"),
            poll_class=read.string(read.required(obj, "poll_class", base), f"{base}.poll_class"),
            timeout_ms=read.optional_integer(obj.get("timeout_ms"), f"{base}.timeout_ms"),
            stale_after_ms=read.optional_integer(
                obj.get("stale_after_ms"), f"{base}.stale_after_ms"
            ),
            retry_policy=read.string(
                read.required(obj, "retry_policy", base), f"{base}.retry_policy"
            ),
            source_timestamp=read.string(
                read.required(obj, "source_timestamp", base), f"{base}.source_timestamp"
            ),
            receive_timestamp=read.string(
                read.required(obj, "receive_timestamp", base), f"{base}.receive_timestamp"
            ),
        )

    def quality(obj: JsonObject, base: str) -> QualitySpec:
        return QualitySpec(
            sentinel=read.string(read.required(obj, "sentinel", base), f"{base}.sentinel"),
            communication_error=read.string(
                read.required(obj, "communication_error", base),
                f"{base}.communication_error",
            ),
            out_of_range=read.string(
                read.required(obj, "out_of_range", base), f"{base}.out_of_range"
            ),
        )

    def command(obj: JsonObject, base: str) -> CommandSpec:
        return CommandSpec(
            status=read.string(read.required(obj, "status", base), f"{base}.status"),
            prerequisites=read.strings(
                read.required(obj, "prerequisites", base), f"{base}.prerequisites"
            ),
            acknowledgement=read.string(
                read.required(obj, "acknowledgement", base), f"{base}.acknowledgement"
            ),
            timeout_ms=read.optional_integer(obj.get("timeout_ms"), f"{base}.timeout_ms"),
            safe_value=read.optional_decimal(obj.get("safe_value"), f"{base}.safe_value"),
            minimum_spacing_ms=read.optional_integer(
                obj.get("minimum_spacing_ms"), f"{base}.minimum_spacing_ms"
            ),
            ramp=read.string(read.required(obj, "ramp", base), f"{base}.ramp"),
        )

    def provenance(obj: JsonObject, base: str) -> PointProvenance:
        return PointProvenance(
            source_id=read.string(read.required(obj, "source_id", base), f"{base}.source_id"),
            source_sha256=read.string(
                read.required(obj, "source_sha256", base), f"{base}.source_sha256"
            ),
            sheet=read.string(read.required(obj, "sheet", base), f"{base}.sheet"),
            row=read.integer(read.required(obj, "row", base), f"{base}.row"),
            cells=read.string(read.required(obj, "cells", base), f"{base}.cells"),
            raw_vendor_name=read.string(
                read.required(obj, "raw_vendor_name", base), f"{base}.raw_vendor_name"
            ),
        )

    def point(obj: JsonObject, base: str) -> PointSpec:
        command_value = obj.get("command")
        return PointSpec(
            logical_id=read.string(read.required(obj, "logical_id", base), f"{base}.logical_id"),
            generated_name=read.string(
                read.required(obj, "generated_name", base), f"{base}.generated_name"
            ),
            vendor_name=read.string(read.required(obj, "vendor_name", base), f"{base}.vendor_name"),
            lifecycle=read.string(read.required(obj, "lifecycle", base), f"{base}.lifecycle"),
            service=read.string(read.required(obj, "service", base), f"{base}.service"),
            access=read.string(read.required(obj, "access", base), f"{base}.access"),
            address=address(
                read.obj(read.required(obj, "address", base), f"{base}.address"),
                f"{base}.address",
            ),
            wire=wire(
                read.obj(read.required(obj, "wire", base), f"{base}.wire"),
                f"{base}.wire",
            ),
            engineering=engineering(
                read.obj(read.required(obj, "engineering", base), f"{base}.engineering"),
                f"{base}.engineering",
            ),
            timing=timing(
                read.obj(read.required(obj, "timing", base), f"{base}.timing"),
                f"{base}.timing",
            ),
            quality=quality(
                read.obj(read.required(obj, "quality", base), f"{base}.quality"),
                f"{base}.quality",
            ),
            command=(
                None
                if command_value is None
                else command(read.obj(command_value, f"{base}.command"), f"{base}.command")
            ),
            provenance=provenance(
                read.obj(read.required(obj, "provenance", base), f"{base}.provenance"),
                f"{base}.provenance",
            ),
            notes=read.strings(read.required(obj, "notes", base), f"{base}.notes"),
        )

    def blocker(obj: JsonObject, base: str) -> BlockerSpec:
        return BlockerSpec(
            blocker_id=read.string(read.required(obj, "blocker_id", base), f"{base}.blocker_id"),
            owner=read.string(read.required(obj, "owner", base), f"{base}.owner"),
            decision_id=read.string(read.required(obj, "decision_id", base), f"{base}.decision_id"),
            source_id=read.string(read.required(obj, "source_id", base), f"{base}.source_id"),
            needed_by=read.string(read.required(obj, "needed_by", base), f"{base}.needed_by"),
            reason=read.string(read.required(obj, "reason", base), f"{base}.reason"),
            missing=read.strings(read.required(obj, "missing", base), f"{base}.missing"),
            known=read.strings(read.required(obj, "known", base), f"{base}.known"),
            affects=read.strings(read.required(obj, "affects", base), f"{base}.affects"),
        )

    device_raw = read.obj(read.required(raw, "device", "$"), "$.device")
    model_raw = read.obj(read.required(device_raw, "model", "$.device"), "$.device.model")
    firmware_raw = read.obj(read.required(device_raw, "firmware", "$.device"), "$.device.firmware")
    map_raw = read.obj(
        read.required(device_raw, "map_revision", "$.device"), "$.device.map_revision"
    )
    device = DeviceSpec(
        device_id=read.string(
            read.required(device_raw, "device_id", "$.device"), "$.device.device_id"
        ),
        display_name=read.string(
            read.required(device_raw, "display_name", "$.device"), "$.device.display_name"
        ),
        role=read.string(read.required(device_raw, "role", "$.device"), "$.device.role"),
        lifecycle=read.string(
            read.required(device_raw, "lifecycle", "$.device"), "$.device.lifecycle"
        ),
        protocol=read.string(
            read.required(device_raw, "protocol", "$.device"), "$.device.protocol"
        ),
        protocol_role=read.string(
            read.required(device_raw, "protocol_role", "$.device"),
            "$.device.protocol_role",
        ),
        endpoint_status=read.string(
            read.required(device_raw, "endpoint_status", "$.device"),
            "$.device.endpoint_status",
        ),
        model=applicability(model_raw, "$.device.model"),
        firmware=applicability(firmware_raw, "$.device.firmware"),
        map_revision=applicability(map_raw, "$.device.map_revision"),
    )
    return ProtocolDocument(
        path=path,
        schema_version=read.string(read.required(raw, "schema_version", "$"), "$.schema_version"),
        document_kind=read.string(read.required(raw, "document_kind", "$"), "$.document_kind"),
        document_id=read.string(read.required(raw, "document_id", "$"), "$.document_id"),
        title=read.string(read.required(raw, "title", "$"), "$.title"),
        lifecycle=read.string(read.required(raw, "lifecycle", "$"), "$.lifecycle"),
        device=device,
        sources=read.items(read.required(raw, "sources", "$"), "$.sources", source),
        reviews=read.items(read.required(raw, "reviews", "$"), "$.reviews", review),
        points=read.items(read.required(raw, "points", "$"), "$.points", point),
        blockers=read.items(read.required(raw, "blockers", "$"), "$.blockers", blocker),
    )
