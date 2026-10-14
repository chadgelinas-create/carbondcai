"""Reviewed schema ownership tests."""

from skid2.persistence.metadata import metadata, system_metadata, telemetry_sample


def test_metadata_owns_only_backend_and_generic_telemetry_tables() -> None:
    """Phase 2 adds one reusable telemetry path and no device-specific schema."""

    assert tuple(metadata.tables) == (
        "system_metadata",
        "telemetry_sample",
        "app_user",
        "user_session",
        "app_config",
        "audit",
        "event",
        "alarm",
        "configuration_revision",
        "configuration_approval",
        "configuration_head",
        "configuration_action",
        "ems_user",
        "account_audit",
        "account_invite",
        "device_config",
        "config_audit",
        "ems_event",
        "ems_alarm",
        "ev_session",
    )
    assert tuple(system_metadata.columns.keys()) == ("key", "value", "created_at")
    assert system_metadata.primary_key.columns.keys() == ["key"]
    assert tuple(telemetry_sample.columns.keys()) == (
        "source_timestamp",
        "id",
        "point_id",
        "label",
        "source",
        "value",
        "unit",
        "receive_timestamp",
        "quality",
        "synthetic",
    )
    assert telemetry_sample.primary_key.columns.keys() == ["source_timestamp", "id"]
    assert {constraint.name for constraint in telemetry_sample.constraints} == {
        "pk_telemetry_sample",
        "uq_telemetry_sample_identity",
    }
    assert {index.name for index in telemetry_sample.indexes} == {
        "ix_telemetry_sample_point_source_time",
        "telemetry_sample_source_timestamp_idx",
    }
