"""EMS decoder and config catalog acceptance (Week 1-5 A/B/C)."""

from __future__ import annotations

from skid2.ems.groups import CONFIG_CATALOG, DEVICE_GROUPS, GROUP_DEVICES, config_spec


def test_all_demo_groups_exist() -> None:
    assert set(DEVICE_GROUPS) >= {"bms", "cnvtr", "solar", "charger", "stabiliti", "supervisor"}


def test_bms_config_730_to_830_in_range() -> None:
    spec = config_spec("bms", "pack_max_voltage")
    assert spec.default == 730.0
    assert spec.minimum is not None and spec.maximum is not None
    assert spec.minimum <= 830.0 <= spec.maximum


def test_charger_thresholds_match_roadmap() -> None:
    assert config_spec("charger", "soc_limited").default == 0.15
    assert config_spec("charger", "soc_maintenance").default == 0.15
    assert config_spec("charger", "soc_terminate").default == 0.10
    assert config_spec("charger", "soc_recover").default == 0.22
    assert config_spec("charger", "session_cap_kwh").default == 10.0


def test_group_device_coverage() -> None:
    assert GROUP_DEVICES["bms"] == ("ba1-1",)
    assert GROUP_DEVICES["charger"] == ("dc600",)
    assert GROUP_DEVICES["stabiliti"] == ("st-4", "st-5", "st-6")


def test_config_catalog_has_mppt_and_stabiliti() -> None:
    keys = {(item.group_id, item.key) for item in CONFIG_CATALOG}
    assert ("solar", "mppt_hold") in keys
    assert ("stabiliti", "st4_power_limit") in keys
