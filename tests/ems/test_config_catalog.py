"""Config service validation tests using an in-memory sqlite substitute.

Timescale migrations require Postgres; these tests exercise pure validation
helpers and catalog bounds without a live database.
"""

from __future__ import annotations

import pytest

from skid2.ems.groups import config_spec, group_config_specs


def test_group_config_specs_non_empty() -> None:
    assert group_config_specs("bms")
    assert group_config_specs("charger")


def test_unknown_config_raises() -> None:
    with pytest.raises(KeyError):
        config_spec("bms", "does_not_exist")


def test_pack_max_voltage_bounds_for_demo() -> None:
    spec = config_spec("bms", "pack_max_voltage")
    assert spec.minimum is not None and spec.maximum is not None
    for value in (730.0, 830.0, 600.0, 900.0):
        assert spec.minimum <= value <= spec.maximum
    assert not (spec.minimum <= 599.0 <= spec.maximum)
