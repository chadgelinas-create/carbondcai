"""Fail-fast and redaction tests for deployment settings."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

import pytest
from pydantic import ValidationError

from skid2.settings import PUBLIC_UI_ORIGINS, Settings, get_settings, load_settings

VALID_ENV = {
    "SKID2_ENVIRONMENT": "test",
    "SKID2_SERVICE_NAME": "backend-test",
    "SKID2_SERVICE_HOST": "127.0.0.1",
    "SKID2_SERVICE_PORT": "8001",
    "SKID2_LOG_LEVEL": "WARNING",
    "SKID2_DATABASE_URL": "postgresql+psycopg://tester:sensitive-value@db:5432/skid2",  # pragma: allowlist secret
}


def set_environment(monkeypatch: pytest.MonkeyPatch, values: Mapping[str, str]) -> None:
    """Replace every project setting in the test process."""

    for key in tuple(os.environ):
        if key.startswith("SKID2_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def test_valid_settings_and_redacted_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid values load once and diagnostic output never contains the password."""

    set_environment(monkeypatch, VALID_ENV)

    settings = get_settings()
    summary = settings.redacted_summary()

    assert settings.environment == "test"
    assert settings.service_port == 8001
    assert settings.simulation_enabled is False
    assert settings.simulation_step_seconds == 1.0
    assert settings.protocol_root == Path("protocol")
    assert summary["database_url"] == "**********"
    assert "sensitive-value" not in repr(settings)
    assert get_settings() is settings


def test_load_settings_can_disable_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tooling can load only the process environment when no file is supplied."""

    set_environment(monkeypatch, VALID_ENV)

    assert load_settings(env_file=None).service_name == "backend-test"


def test_load_settings_from_explicit_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Local tooling can opt into one explicit environment file."""

    set_environment(monkeypatch, {})
    env_file = tmp_path / "test.env"
    env_file.write_text(
        "\n".join(
            (
                "SKID2_ENVIRONMENT=test",
                "SKID2_SERVICE_NAME=file-test",
                "SKID2_SERVICE_HOST=127.0.0.1",
                "SKID2_SERVICE_PORT=8010",
                "SKID2_DATABASE_URL=postgresql+psycopg://file:fake-test-value@db:5432/skid2",  # pragma: allowlist secret
            )
        ),
        encoding="utf-8",
    )

    assert load_settings(env_file).service_name == "file-test"


def test_missing_required_setting_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing database URL prevents startup with a field-level error."""

    incomplete = {key: value for key, value in VALID_ENV.items() if key != "SKID2_DATABASE_URL"}
    set_environment(monkeypatch, incomplete)

    with pytest.raises(ValidationError, match="database_url"):
        Settings()


@pytest.mark.parametrize(
    "database_url",
    [
        "mysql://db/skid2",
        "postgresql+psycopg:///skid2",
        "postgresql+psycopg://db",
    ],
)
def test_malformed_database_url_fails(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    """Wrong schemes or incomplete URLs fail before readiness."""

    set_environment(monkeypatch, {**VALID_ENV, "SKID2_DATABASE_URL": database_url})

    with pytest.raises(ValidationError, match="database URL"):
        Settings()


def test_invalid_type_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed scalar values fail before the process reports ready."""

    set_environment(monkeypatch, {**VALID_ENV, "SKID2_SERVICE_PORT": "not-a-port"})

    with pytest.raises(ValidationError, match="service_port"):
        Settings()


def test_production_rejects_published_local_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production cannot fall back to the example development credential."""

    unsafe = {
        **VALID_ENV,
        "SKID2_ENVIRONMENT": "production",
        "SKID2_DATABASE_URL": "postgresql+psycopg://skid2:local-development-only@db:5432/skid2",  # pragma: allowlist secret
    }
    set_environment(monkeypatch, unsafe)

    with pytest.raises(ValidationError, match="local-development credential") as error:
        Settings()
    assert "postgresql" not in str(error.value)


def test_production_rejects_synthetic_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Phase 2 test generator can never be enabled in production."""

    unsafe = {
        **VALID_ENV,
        "SKID2_ENVIRONMENT": "production",
        "SKID2_SYNTHETIC_ENABLED": "true",
        "SKID2_AUTH_SECRET": "production-auth-secret-value",
    }
    set_environment(monkeypatch, unsafe)

    with pytest.raises(ValidationError, match="forbidden in production"):
        Settings()


def test_production_rejects_simulation_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Simulator Lab can never be enabled in production."""

    unsafe = {
        **VALID_ENV,
        "SKID2_ENVIRONMENT": "production",
        "SKID2_SIMULATION_ENABLED": "true",
        "SKID2_AUTH_SECRET": "production-auth-secret-value",
    }
    set_environment(monkeypatch, unsafe)

    with pytest.raises(ValidationError, match="simulation controls are forbidden"):
        Settings()


def test_production_rejects_local_auth_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    unsafe = {
        **VALID_ENV,
        "SKID2_ENVIRONMENT": "production",
        "SKID2_AUTH_SECRET": "local-development-auth-secret",
    }
    set_environment(monkeypatch, unsafe)
    with pytest.raises(ValidationError, match="local-development auth secret"):
        Settings()


def test_modbus_requires_simulator_lab(monkeypatch: pytest.MonkeyPatch) -> None:
    set_environment(monkeypatch, {**VALID_ENV, "SKID2_MODBUS_ENABLED": "true"})

    with pytest.raises(ValidationError, match="requires the Simulator Lab"):
        Settings()


def test_modbus_device_ids_must_be_unique(monkeypatch: pytest.MonkeyPatch) -> None:
    set_environment(
        monkeypatch,
        {
            **VALID_ENV,
            "SKID2_MODBUS_BA1_ID": "2",
            "SKID2_MODBUS_CNVTR1_ID": "2",
        },
    )

    with pytest.raises(ValidationError, match="device IDs must be different"):
        Settings()


def test_freshness_threshold_must_exceed_generation_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configuration cannot make normally scheduled samples immediately stale."""

    unsafe = {
        **VALID_ENV,
        "SKID2_SYNTHETIC_INTERVAL_SECONDS": "3",
        "SKID2_FRESHNESS_THRESHOLD_SECONDS": "3",
    }
    set_environment(monkeypatch, unsafe)

    with pytest.raises(ValidationError, match="must exceed"):
        Settings()


def test_simulation_requires_an_explicit_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    set_environment(monkeypatch, {**VALID_ENV, "SKID2_SIMULATION_ENABLED": "true"})
    with pytest.raises(ValidationError, match="explicit seed"):
        Settings()
    monkeypatch.setenv("SKID2_SIMULATION_SEED", "0")
    assert Settings().simulation_seed == 0
    monkeypatch.setenv("SKID2_SIMULATION_PROFILE", "production")
    with pytest.raises(ValidationError, match="simulation_profile"):
        Settings()


def test_login_origins_pair_localhost_in_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_environment(monkeypatch, {**VALID_ENV, "SKID2_ENVIRONMENT": "development"})

    origins = get_settings().login_origins()

    assert origins == frozenset({"http://localhost:5173", "http://127.0.0.1:5173", *PUBLIC_UI_ORIGINS})


def test_login_origins_stay_exact_outside_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_environment(monkeypatch, VALID_ENV)

    assert get_settings().login_origins() == frozenset({"http://localhost:5173", *PUBLIC_UI_ORIGINS})
