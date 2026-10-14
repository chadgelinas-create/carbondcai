"""Appendix A holdings: INT32 is two words, MSB big-endian as stated."""

from decimal import Decimal

from skid2.ems.charger_registers import HOLDINGS, pack, unpack
from skid2.protocol.codecs import CodecSpec, encode_value


def _by_display() -> dict[str, object]:
    return {item.display: item for item in HOLDINGS}


def test_appendix_a_addresses_and_word_spans() -> None:
    by_id = _by_display()
    assert by_id["40001"].wire_offset == 0
    assert by_id["40001"].codec.word_count == 2
    assert by_id["40003"].wire_offset == 2
    assert by_id["40004"].wire_offset == 3
    assert by_id["40005"].wire_offset == 4
    assert by_id["40101"].wire_offset == 100
    assert by_id["40103"].wire_offset == 102
    assert by_id["40104"].wire_offset == 103
    assert by_id["40105"].wire_offset == 104
    assert by_id["40107"].wire_offset == 106


def test_int32_demand_is_msb_big_endian_not_wago_low_word_first() -> None:
    holding = _by_display()["40001"]
    words = pack(holding, 350_000)
    assert words == (0x0005, 0x5730)
    assert unpack(holding, words) == Decimal(350_000)
    wago_little = encode_value(
        350_000,
        CodecSpec(
            primitive="int32",
            word_count=2,
            byte_order="big",
            word_order="little",
            scale=Decimal(1),
            rounding="reject",
        ),
    )
    assert wago_little == (0x5730, 0x0005)
    assert words != wago_little


def test_available_power_and_scaled_bus_voltage() -> None:
    available = _by_display()["40101"]
    assert pack(available, 600_000) == (0x0009, 0x27C0)
    bus = _by_display()["40005"]
    assert unpack(bus, pack(bus, Decimal("750.0"))) == Decimal("750.0")
    soc = _by_display()["40107"]
    assert unpack(soc, pack(soc, Decimal("62.4"))) == Decimal("62.4")
