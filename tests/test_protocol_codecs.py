"""Primitive codec boundaries and independent golden vectors."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from skid2.protocol.codecs import (
    CodecError,
    CodecSpec,
    decode_words,
    encode_value,
    extract_bit,
    merge_bit,
    raw_bounds,
    vendor_charge_positive_to_internal_discharge_positive,
)

ROOT = Path(__file__).parents[1]


def _spec(
    primitive: str,
    byte_order: str = "big",
    word_order: str = "big",
    **changes: object,
) -> CodecSpec:
    words = 2 if primitive.endswith("32") else 1
    values: dict[str, object] = {
        "primitive": primitive,
        "word_count": words,
        "byte_order": byte_order,
        "word_order": word_order,
    }
    values.update(changes)
    return CodecSpec(**values)  # type: ignore[arg-type]


def test_independent_golden_vectors_encode_and_decode() -> None:
    """Every supported integer/order vector matches hand-calculated words."""

    fixture = json.loads((ROOT / "protocol/golden-vectors.json").read_text())
    for vector in fixture["vectors"]:
        spec = _spec(vector["primitive"], vector["byte_order"], vector["word_order"])
        words = tuple(vector["words"])
        assert encode_value(Decimal(vector["raw"]), spec) == words, vector["name"]
        decoded = decode_words(words, spec)
        assert decoded.value == Decimal(vector["raw"]), vector["name"]
        assert decoded.quality == "good"


@pytest.mark.parametrize("primitive", ["uint16", "int16", "uint32", "int32"])
def test_raw_boundaries_round_trip(primitive: str) -> None:
    """Minimum, maximum, and zero retain exact value."""

    spec = _spec(primitive)
    minimum, maximum = raw_bounds(spec)
    for value in {minimum, maximum, 0}:
        words = encode_value(value, spec)
        assert decode_words(words, spec).value == Decimal(value)


def test_scaling_offset_quantization_and_range() -> None:
    """Engineering conversion has explicit exact and rounded behavior."""

    exact = _spec(
        "int16",
        scale=Decimal("0.1"),
        offset=Decimal("-40"),
        minimum=Decimal("-40"),
        maximum=Decimal("125"),
    )
    assert encode_value("-12.3", exact) == (277,)
    assert decode_words((277,), exact).value == Decimal("-12.3")
    with pytest.raises(CodecError, match="exactly representable"):
        encode_value("-12.35", exact)

    half_even = _spec("uint16", scale=Decimal("0.1"), rounding="half_even")
    half_up = _spec("uint16", scale=Decimal("0.1"), rounding="half_up")
    assert encode_value("1.25", half_even) == (12,)
    assert encode_value("1.25", half_up) == (13,)
    with pytest.raises(CodecError, match="below"):
        encode_value("-41", exact)
    with pytest.raises(CodecError, match="above"):
        encode_value("126", exact)


def test_sentinel_and_out_of_range_quality() -> None:
    """Invalid raw values cannot masquerade as plausible engineering values."""

    spec = _spec(
        "uint16",
        sentinels=(65535,),
        minimum=Decimal(0),
        maximum=Decimal(100),
    )
    sentinel = decode_words((65535,), spec)
    assert sentinel.value is None
    assert sentinel.quality == "invalid"
    assert decode_words((101,), spec).quality == "out_of_range"
    sentinel_only = _spec("uint16", sentinels=(65535,))
    with pytest.raises(CodecError, match="sentinel"):
        encode_value(65535, sentinel_only)


def test_bit_extract_merge_and_decode_preserve_other_bits() -> None:
    """Bit writes change only the requested bit."""

    assert extract_bit(0b1010, 1)
    assert not extract_bit(0b1010, 0)
    assert merge_bit(0b1010, 0, True) == 0b1011
    assert merge_bit(0b1010, 1, False) == 0b1000
    bit = _spec("bit", bit=3)
    assert decode_words((0b1000,), bit).value == Decimal(1)
    with pytest.raises(CodecError, match="merge_bit"):
        encode_value(1, bit)


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        (_spec("uint16", word_count=2), "requires"),
        (_spec("uint16", byte_order="middle"), "byte order"),
        (_spec("uint16", word_order="middle"), "word order"),
        (_spec("uint16", scale=Decimal(0)), "scale"),
        (_spec("uint16", rounding="guess"), "rounding"),
        (_spec("bit"), "bit index"),
        (_spec("uint16", bit=0), "bit index"),
        (_spec("float32"), "unsupported primitive"),
    ],
)
def test_invalid_codec_contracts_fail(spec: CodecSpec, message: str) -> None:
    """Unsupported or ambiguous wire specifications fail explicitly."""

    with pytest.raises(CodecError, match=message):
        raw_bounds(spec)


def test_invalid_values_words_and_bits_fail() -> None:
    """Overflow, non-finite, word-count, register, and bit errors are typed."""

    spec = _spec("uint16")
    with pytest.raises(CodecError, match="finite"):
        encode_value("NaN", spec)
    with pytest.raises(CodecError, match="outside"):
        encode_value(65536, spec)
    with pytest.raises(CodecError, match="expected"):
        decode_words((), spec)
    with pytest.raises(CodecError, match="outside"):
        decode_words((65536,), spec)
    with pytest.raises(CodecError, match=r"0\.\.65535"):
        extract_bit(-1, 0)
    with pytest.raises(CodecError, match=r"0\.\.15"):
        extract_bit(0, 16)


def test_sign_conversion_is_explicit_and_involutive() -> None:
    """Vendor-positive-charge changes sign only at the named adapter."""

    vendor = Decimal("25.5")
    internal = vendor_charge_positive_to_internal_discharge_positive(vendor)
    assert internal == Decimal("-25.5")
    assert vendor_charge_positive_to_internal_discharge_positive(internal) == vendor
