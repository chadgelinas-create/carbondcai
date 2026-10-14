"""Single normalization boundary for Modbus-style register addresses."""

from __future__ import annotations

from dataclasses import dataclass

REGISTER_SPACES = frozenset({"coil", "discrete_input", "input_register", "holding_register"})
NOTATIONS = frozenset({"decimal", "hex", "modicon"})
CONVENTIONS = frozenset({"zero_based", "one_based", "display_reference"})
MAX_PDU_OFFSET = 65_535


class AddressError(ValueError):
    """An ambiguous or invalid protocol address."""


@dataclass(frozen=True, slots=True, order=True)
class CanonicalAddress:
    """A transport-ready PDU offset plus its unambiguous register context."""

    space: str
    offset: int
    word_count: int
    bit: int | None = None

    @property
    def occupied_offsets(self) -> tuple[int, ...]:
        """Return every register occupied by the value."""

        return tuple(range(self.offset, self.offset + self.word_count))


def _parse_number(display: str, notation: str) -> int:
    cleaned = display.strip().replace("_", "")
    try:
        if notation == "hex":
            return int(cleaned, 16)
        if notation in {"decimal", "modicon"}:
            if not cleaned.isdecimal():
                raise ValueError
            return int(cleaned, 10)
    except ValueError as exc:
        raise AddressError(f"invalid {notation} address {display!r}") from exc
    raise AddressError(f"unsupported address notation {notation!r}")


def _modicon_offset(value: int, declared_space: str) -> tuple[str, int]:
    ranges = (
        ("coil", 1, 10_000),
        ("discrete_input", 10_001, 20_000),
        ("input_register", 30_001, 40_000),
        ("holding_register", 40_001, 50_000),
    )
    for derived_space, start, end in ranges:
        if start <= value <= end:
            if declared_space != derived_space:
                raise AddressError(
                    f"display address {value} is {derived_space}, not {declared_space}"
                )
            return derived_space, value - start
    raise AddressError(f"unsupported Modicon display reference {value}")


def normalize_address(
    *,
    display: str,
    notation: str,
    convention: str,
    space: str,
    word_count: int,
    bit: int | None = None,
) -> CanonicalAddress:
    """Normalize a display address to a zero-based PDU offset."""

    if notation not in NOTATIONS:
        raise AddressError(f"unsupported address notation {notation!r}")
    if convention not in CONVENTIONS:
        raise AddressError(f"unsupported address convention {convention!r}")
    if space not in REGISTER_SPACES:
        raise AddressError(f"unsupported register space {space!r}")
    if word_count <= 0:
        raise AddressError("word count must be positive")

    numeric = _parse_number(display, notation)
    if notation == "modicon":
        if convention != "display_reference":
            raise AddressError("Modicon notation requires display_reference convention")
        normalized_space, offset = _modicon_offset(numeric, space)
    else:
        if convention == "display_reference":
            raise AddressError("display_reference convention requires Modicon notation")
        normalized_space = space
        offset = numeric if convention == "zero_based" else numeric - 1

    if offset < 0 or offset > MAX_PDU_OFFSET:
        raise AddressError(f"wire offset {offset} is outside 0..{MAX_PDU_OFFSET}")
    end = offset + word_count - 1
    if end > MAX_PDU_OFFSET:
        raise AddressError(f"word span {offset}..{end} exceeds {MAX_PDU_OFFSET}")
    if bit is not None:
        if word_count != 1:
            raise AddressError("bit addressing requires exactly one word")
        if bit < 0 or bit > 15:
            raise AddressError("bit index must be in 0..15")

    return CanonicalAddress(normalized_space, offset, word_count, bit)


def format_display(address: CanonicalAddress, notation: str, convention: str) -> str:
    """Format a canonical address without guessing a base convention."""

    if notation == "modicon":
        if convention != "display_reference":
            raise AddressError("Modicon notation requires display_reference convention")
        bases = {
            "coil": 1,
            "discrete_input": 10_001,
            "input_register": 30_001,
            "holding_register": 40_001,
        }
        return str(bases[address.space] + address.offset)
    if convention == "display_reference":
        raise AddressError("display_reference convention requires Modicon notation")
    value = address.offset if convention == "zero_based" else address.offset + 1
    if notation == "hex":
        return f"0x{value:04X}"
    if notation == "decimal":
        return str(value)
    raise AddressError(f"unsupported address notation {notation!r}")
