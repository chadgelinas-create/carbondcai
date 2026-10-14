"""Reusable image command tests."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from skid2 import cli
from skid2.persistence.database import DatabaseStatus
from skid2.settings import Settings


def settings() -> Settings:
    """Create settings with a value that must remain redacted."""

    return Settings(
        environment="test",
        service_name="test",
        service_host="127.0.0.1",
        service_port=8000,
        database_url=SecretStr("postgresql+psycopg://user:sensitive@db:5432/skid2"),
    )


def test_check_settings_is_redacted(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The settings command is useful without printing credentials."""

    monkeypatch.setattr(cli, "get_settings", settings)

    assert cli.main(["check-settings"]) == 0
    output = capsys.readouterr().out
    assert "**********" in output
    assert "sensitive" not in output


@pytest.mark.parametrize("command", ["check-database", "show-revision"])
def test_database_commands(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    """The same image supports readiness and revision commands."""

    monkeypatch.setattr(cli, "get_settings", settings)
    monkeypatch.setattr(
        cli,
        "database_status",
        lambda _settings: DatabaseStatus("20260721_0001", "2.28.2"),
    )

    assert cli.main([command]) == 0
    output = capsys.readouterr().out
    assert "20260721_0001" in output


def test_import_smoke_command(capsys: pytest.CaptureFixture[str]) -> None:
    """Package imports can be checked without database access."""

    assert cli.run("import-smoke") == 0
    assert "imported 7 package boundaries" in capsys.readouterr().out


def test_unknown_run_command_is_rejected() -> None:
    """Programmatic callers cannot silently select an unknown operation."""

    with pytest.raises(ValueError, match="unsupported backend command"):
        cli.run("unknown")
