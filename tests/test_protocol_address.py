"""Address normalization boundaries and round trips."""

from __future__ import annotations

import pytest

from skid2.protocol.address import AddressError, format_display, normalize_address


@pytest.mark.parametrize(
    ("display", "notation", "convention", "space", "offset"),
    [
        ("0", "decimal", "zero_based", "holding_register", 0),
        ("1", "decimal", "one_based", "holding_register", 0),
        ("0x0000", "hex", "zero_based", "input_register", 0),
        ("0x0001", "hex", "one_based", "input_register", 0),
        ("30001", "modicon", "display_reference", "input_register", 0),
        ("40001", "modicon", "display_reference", "holding_register", 0),
        ("0xFFFF", "hex", "zero_based", "holding_register", 65535),
    ],
)
def test_address_forms_normalize_and_round_trip(
    display: str,
    notation: str,
    convention: str,
    space: str,
    offset: int,
) -> None:
    """Equivalent notations reach one canonical offset and return exactly."""

    address = normalize_address(
        display=display,
        notation=notation,
        convention=convention,
        space=space,
        word_count=1,
    )
    assert address.offset == offset
    assert format_display(address, notation, convention).lower() == display.lower()


def test_multiword_and_bit_occupied_ranges() -> None:
    """Spans and bits are deterministic."""

    multi = normalize_address(
        display="0x0010",
        notation="hex",
        convention="zero_based",
        space="input_register",
        word_count=2,
    )
    bit = normalize_address(
        display="16",
        notation="decimal",
        convention="zero_based",
        space="input_register",
        word_count=1,
        bit=15,
    )
    assert multi.occupied_offsets == (16, 17)
    assert bit.bit == 15


@pytest.mark.parametrize(
    "arguments",
    [
        {"display": "0", "notation": "decimal", "convention": "one_based"},
        {"display": "-1", "notation": "decimal", "convention": "zero_based"},
        {"display": "0x10000", "notation": "hex", "convention": "zero_based"},
        {"display": "65535", "notation": "decimal", "convention": "zero_based", "word_count": 2},
        {"display": "40001", "notation": "modicon", "convention": "zero_based"},
        {
            "display": "40001",
            "notation": "modicon",
            "convention": "display_reference",
            "space": "input_register",
        },
        {"display": "10", "notation": "decimal", "convention": "display_reference"},
        {"display": "10", "notation": "decimal", "convention": "zero_based", "bit": 16},
        {
            "display": "10",
            "notation": "decimal",
            "convention": "zero_based",
            "word_count": 2,
            "bit": 1,
        },
        {"display": "ten", "notation": "decimal", "convention": "zero_based"},
        {"display": "10", "notation": "octal", "convention": "zero_based"},
        {"display": "10", "notation": "decimal", "convention": "mystery"},
        {"display": "10", "notation": "decimal", "convention": "zero_based", "space": "mystery"},
        {"display": "10", "notation": "decimal", "convention": "zero_based", "word_count": 0},
    ],
)
def test_ambiguous_and_out_of_range_addresses_fail(arguments: dict[str, object]) -> None:
    """No invalid base, span, context, or bit is silently repaired."""

    values: dict[str, object] = {
        "display": "10",
        "notation": "decimal",
        "convention": "zero_based",
        "space": "holding_register",
        "word_count": 1,
        "bit": None,
    }
    values.update(arguments)
    with pytest.raises(AddressError):
        normalize_address(**values)  # type: ignore[arg-type]


def test_format_rejects_ambiguous_requests() -> None:
    """Canonical values are not formatted through an incompatible convention."""

    address = normalize_address(
        display="0",
        notation="decimal",
        convention="zero_based",
        space="coil",
        word_count=1,
    )
    with pytest.raises(AddressError):
        format_display(address, "decimal", "display_reference")
    with pytest.raises(AddressError):
        format_display(address, "octal", "zero_based")
