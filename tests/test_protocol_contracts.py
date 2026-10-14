"""Generated point-level candidate contract execution."""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path
from types import ModuleType

from skid2.protocol.codecs import CodecSpec, decode_words, encode_value, raw_bounds
from skid2.protocol.loader import parse_document
from skid2.protocol.models import PointSpec

ROOT = Path(__file__).parents[1]


def _generated() -> ModuleType:
    target = ROOT / "generated/protocol/python/point_descriptors.py"
    spec = importlib.util.spec_from_file_location("phase3_contract_points", target)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _spec(point: PointSpec) -> CodecSpec:
    # The helper is intentionally local so generated fixtures remain independent
    # from runtime transport or device behavior.
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


def test_every_candidate_point_executes_raw_and_engineering_contract() -> None:
    """Each selected candidate point is tested exactly once at applicable bounds."""

    documents = [
        parse_document(path) for path in sorted((ROOT / "protocol/devices").glob("*.yaml"))
    ]
    points = [
        point
        for document in documents
        for point in document.points
        if point.lifecycle == "candidate"
    ]
    generated = _generated()
    assert set(generated.CODEC_SPECS) == {point.logical_id for point in points}
    tested: set[str] = set()
    for point in points:
        assert point.logical_id not in tested
        tested.add(point.logical_id)
        expected_spec = _spec(point)
        spec = generated.CODEC_SPECS[point.logical_id]
        assert spec == expected_spec
        raw_minimum, raw_maximum = raw_bounds(spec)
        if point.wire.primitive == "bit":
            assert decode_words((0,), spec).value == Decimal(0)
            assert decode_words((1 << (point.address.bit or 0),), spec).value == Decimal(1)
            continue

        candidates = {
            point.engineering.minimum,
            point.engineering.maximum,
            Decimal(0),
        }
        for value in candidates:
            if value is None:
                continue
            if point.engineering.minimum is not None and value < point.engineering.minimum:
                continue
            if point.engineering.maximum is not None and value > point.engineering.maximum:
                continue
            raw = (value - point.engineering.offset) / point.engineering.scale
            if raw != raw.to_integral_value():
                continue
            if not raw_minimum <= int(raw) <= raw_maximum:
                continue
            if int(raw) in point.engineering.sentinels:
                continue
            words = encode_value(value, spec)
            decoded = decode_words(words, spec)
            assert decoded.value == value, (
                point.logical_id,
                point.provenance.sheet,
                point.provenance.row,
            )
            assert decoded.quality == "good"

    assert tested
    assert tested == {point.logical_id for point in points}
