"""Appendix A PMS <-> EVCS holdings for SIM.

INT32 is two holding registers, MSB big-endian (byte_order=big, word_order=big)
as stated in the clarification. Exact LIVE addresses still go to Siemens/EVCS.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from skid2.protocol.codecs import CodecSpec, decode_words, encode_value

INT32_BE = CodecSpec(
    primitive="int32",
    word_count=2,
    byte_order="big",
    word_order="big",
    scale=Decimal(1),
    offset=Decimal(0),
    rounding="reject",
)
UINT16_BE = CodecSpec(
    primitive="uint16",
    word_count=1,
    byte_order="big",
    word_order="big",
    scale=Decimal(1),
    offset=Decimal(0),
    rounding="reject",
)
VOLTS_X10 = CodecSpec(
    primitive="int32",
    word_count=2,
    byte_order="big",
    word_order="big",
    scale=Decimal("0.1"),
    offset=Decimal(0),
    rounding="reject",
)
SOC_X10 = CodecSpec(
    primitive="uint16",
    word_count=1,
    byte_order="big",
    word_order="big",
    scale=Decimal("0.1"),
    offset=Decimal(0),
    rounding="reject",
)


@dataclass(frozen=True, slots=True)
class Holding:
    display: str
    logical_id: str
    name: str
    access: str
    codec: CodecSpec
    unit: str
    wire_offset: int


# Modicon 4xxxx display_reference -> PDU offset 40001==0, 40101==100.
HOLDINGS: tuple[Holding, ...] = (
    Holding("40001", "dc600.evcs.demand.power", "EV Charger Power Demand", "read", INT32_BE, "W", 0),
    Holding("40003", "dc600.evcs.status", "EV Charger Status", "read", UINT16_BE, "enum", 2),
    Holding("40004", "dc600.evcs.vehicles", "Connected Vehicles", "read", UINT16_BE, "count", 3),
    Holding("40005", "dc600.evcs.bus.voltage", "DC Bus Voltage", "read", VOLTS_X10, "V", 4),
    Holding("40101", "dc600.pms.available.power", "Available Power", "write", INT32_BE, "W", 100),
    Holding("40103", "dc600.pms.system.status", "System Status", "write", UINT16_BE, "enum", 102),
    Holding("40104", "dc600.pms.time.remaining", "Time Remaining", "write", UINT16_BE, "min", 103),
    Holding("40105", "dc600.pms.pv.power", "PV Power", "write", INT32_BE, "W", 104),
    Holding("40107", "dc600.pms.bess.soc", "BESS SOC", "write", SOC_X10, "%", 106),
)

CHARGER_STATUS_IDLE = 0
CHARGER_STATUS_CHARGING = 1
CHARGER_STATUS_FAULT = 2


def kw_to_w(kw: float) -> int:
    return int(round(max(0.0, kw) * 1000.0))


def pack(holding: Holding, value: Decimal | int | float | str) -> tuple[int, ...]:
    return encode_value(value, holding.codec)


def unpack(holding: Holding, words: tuple[int, ...]) -> Decimal | None:
    return decode_words(words, holding.codec).value


def charger_status_code(*, occupied: bool, demand_kw: float, fault: bool, comms_ok: bool) -> int:
    if fault or not comms_ok:
        return CHARGER_STATUS_FAULT
    if occupied and demand_kw > 0:
        return CHARGER_STATUS_CHARGING
    return CHARGER_STATUS_IDLE
