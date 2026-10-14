"""Transport-independent primitive register codecs."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import (
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    Decimal,
    InvalidOperation,
)

from skid2.protocol.models import PointSpec

PRIMITIVE_WIDTHS = {
    "bit": (1, False),
    "bitfield": (1, False),
    "uint16": (1, False),
    "int16": (1, True),
    "uint32": (2, False),
    "int32": (2, True),
}
ROUNDING_MODES = {
    "reject": None,
    "half_even": ROUND_HALF_EVEN,
    "half_up": ROUND_HALF_UP,
}


class CodecError(ValueError):
    """A typed encoding or decoding failure."""


@dataclass(frozen=True, slots=True)
class CodecSpec:
    """Complete primitive and engineering conversion contract."""

    primitive: str
    word_count: int
    byte_order: str
    word_order: str
    scale: Decimal = Decimal(1)
    offset: Decimal = Decimal(0)
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    sentinels: tuple[int, ...] = ()
    rounding: str = "reject"
    bit: int | None = None


@dataclass(frozen=True, slots=True)
class DecodedValue:
    """Decoded value plus the quality decision caused by the raw input."""

    value: Decimal | None
    raw: int
    quality: str


def _validated(spec: CodecSpec) -> tuple[int, bool]:
    try:
        expected_words, signed = PRIMITIVE_WIDTHS[spec.primitive]
    except KeyError as exc:
        raise CodecError(f"unsupported primitive {spec.primitive!r}") from exc
    if spec.word_count != expected_words:
        raise CodecError(
            f"{spec.primitive} requires {expected_words} word(s), got {spec.word_count}"
        )
    if spec.byte_order not in {"big", "little"}:
        raise CodecError(f"unsupported byte order {spec.byte_order!r}")
    if spec.word_order not in {"big", "little"}:
        raise CodecError(f"unsupported word order {spec.word_order!r}")
    if spec.scale == 0 or not spec.scale.is_finite() or not spec.offset.is_finite():
        raise CodecError("scale must be finite and non-zero and offset must be finite")
    if spec.rounding not in ROUNDING_MODES:
        raise CodecError(f"unsupported rounding policy {spec.rounding!r}")
    if spec.primitive == "bit":
        if spec.bit is None or not 0 <= spec.bit <= 15:
            raise CodecError("bit primitive requires bit index 0..15")
    elif spec.bit is not None:
        raise CodecError("bit index is valid only for bit primitive")
    return expected_words * 16, signed


def raw_bounds(spec: CodecSpec) -> tuple[int, int]:
    """Return inclusive integer bounds for a primitive."""

    width, signed = _validated(spec)
    if spec.primitive == "bit":
        return 0, 1
    if signed:
        return -(1 << (width - 1)), (1 << (width - 1)) - 1
    return 0, (1 << width) - 1


def _words_to_canonical_bytes(words: tuple[int, ...], spec: CodecSpec) -> bytes:
    if len(words) != spec.word_count:
        raise CodecError(f"expected {spec.word_count} words, got {len(words)}")
    chunks: list[bytes] = []
    for word in words:
        if isinstance(word, bool) or not 0 <= word <= 0xFFFF:
            raise CodecError(f"register word {word!r} is outside 0..65535")
        chunk = word.to_bytes(2, "big")
        chunks.append(chunk if spec.byte_order == "big" else chunk[::-1])
    if spec.word_order == "little":
        chunks.reverse()
    return b"".join(chunks)


def _raw_to_words(raw: int, spec: CodecSpec) -> tuple[int, ...]:
    width, signed = _validated(spec)
    minimum, maximum = raw_bounds(spec)
    if raw < minimum or raw > maximum:
        raise CodecError(f"raw value {raw} is outside {minimum}..{maximum}")
    if spec.primitive == "bit":
        raise CodecError("bit writes require merge_bit to preserve unrelated bits")
    try:
        data = raw.to_bytes(width // 8, "big", signed=signed)
    except OverflowError as exc:
        raise CodecError(f"raw value {raw} cannot be encoded") from exc
    chunks = [data[index : index + 2] for index in range(0, len(data), 2)]
    if spec.word_order == "little":
        chunks.reverse()
    if spec.byte_order == "little":
        chunks = [chunk[::-1] for chunk in chunks]
    return tuple(int.from_bytes(chunk, "big") for chunk in chunks)


def decode_words(words: tuple[int, ...], spec: CodecSpec) -> DecodedValue:
    """Decode register words into an engineering value and quality."""

    width, signed = _validated(spec)
    data = _words_to_canonical_bytes(words, spec)
    register_raw = int.from_bytes(data, "big", signed=signed)
    raw = (
        (register_raw >> spec.bit) & 1
        if spec.primitive == "bit" and spec.bit is not None
        else register_raw
    )
    if raw in spec.sentinels:
        return DecodedValue(value=None, raw=raw, quality="invalid")
    value = Decimal(raw) * spec.scale + spec.offset
    if (spec.minimum is not None and value < spec.minimum) or (
        spec.maximum is not None and value > spec.maximum
    ):
        return DecodedValue(value=value, raw=raw, quality="out_of_range")
    if width not in {16, 32}:
        raise CodecError("internal primitive width is unsupported")
    return DecodedValue(value=value, raw=raw, quality="good")


def encode_value(value: Decimal | int | str, spec: CodecSpec) -> tuple[int, ...]:
    """Encode an engineering value using the declared quantization policy."""

    _validated(spec)
    try:
        engineering = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CodecError(f"invalid engineering value {value!r}") from exc
    if not engineering.is_finite():
        raise CodecError("engineering value must be finite")
    if spec.minimum is not None and engineering < spec.minimum:
        raise CodecError(f"engineering value {engineering} is below {spec.minimum}")
    if spec.maximum is not None and engineering > spec.maximum:
        raise CodecError(f"engineering value {engineering} is above {spec.maximum}")

    raw_decimal = (engineering - spec.offset) / spec.scale
    integral = raw_decimal.to_integral_value()
    if raw_decimal != integral:
        mode = ROUNDING_MODES[spec.rounding]
        if mode is None:
            raise CodecError(f"engineering value {engineering} is not exactly representable")
        integral = raw_decimal.to_integral_value(rounding=mode)
    raw = int(integral)
    if raw in spec.sentinels:
        raise CodecError(f"raw value {raw} is reserved as a sentinel")
    return _raw_to_words(raw, spec)


def extract_bit(word: int, bit: int) -> bool:
    """Extract one register bit."""

    if not 0 <= word <= 0xFFFF:
        raise CodecError("word must be in 0..65535")
    if not 0 <= bit <= 15:
        raise CodecError("bit must be in 0..15")
    return bool((word >> bit) & 1)


def merge_bit(word: int, bit: int, enabled: bool) -> int:
    """Change one bit while preserving every unrelated bit."""

    extract_bit(word, bit)
    mask = 1 << bit
    return word | mask if enabled else word & ~mask


def vendor_charge_positive_to_internal_discharge_positive(value: Decimal) -> Decimal:
    """Named DCDC adapter boundary; generic codecs never change power direction."""

    return -value


def codec_spec(point: PointSpec) -> CodecSpec:
    return CodecSpec(
        primitive=point.wire.primitive,
        word_count=point.wire.word_count,
        byte_order=point.wire.byte_order,
        word_order=point.wire.word_order,
        scale=point.engineering.scale,
        offset=point.engineering.offset,
        minimum=point.engineering.minimum,
        maximum=point.engineering.maximum,
        sentinels=point.engineering.sentinels,
        rounding=point.engineering.rounding,
        bit=point.address.bit,
    )
