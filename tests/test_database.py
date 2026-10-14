"""Database check tests without requiring a live service."""

from __future__ import annotations

from typing import Self

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine

from skid2.persistence import database
from skid2.settings import Settings


def settings() -> Settings:
    """Create isolated settings without reading local files."""

    return Settings(
        environment="test",
        service_name="test",
        service_host="127.0.0.1",
        service_port=8000,
        database_url=SecretStr("postgresql+psycopg://user:secret@db:5432/skid2"),
    )


class ScalarResult:
    """Minimal scalar result used by the database check."""

    def __init__(self, value: str) -> None:
        self.value = value

    def scalar_one(self) -> str:
        """Return the configured single value."""

        return self.value


class FakeConnection:
    """Context-managed connection returning values by query meaning."""

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: object) -> ScalarResult:
        """Return the matching revision or extension version."""

        if "alembic_version" in str(statement):
            return ScalarResult("20260721_0001")
        return ScalarResult("2.28.2")


class FakeEngine:
    """Engine double that records disposal."""

    def __init__(self) -> None:
        self.disposed = False

    def connect(self) -> FakeConnection:
        """Open the fake connection."""

        return FakeConnection()

    def dispose(self) -> None:
        """Record deterministic cleanup."""

        self.disposed = True


def test_build_engine_hides_parameters() -> None:
    """The real engine is constructed without exposing its password in repr."""

    engine = database.build_engine(settings())
    try:
        assert isinstance(engine, Engine)
        assert "***" in str(engine.url)
        assert "secret" not in str(engine.url)
    finally:
        engine.dispose()


def test_database_status_reads_revision_and_extension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readiness includes both migration and TimescaleDB state and disposes resources."""

    engine = FakeEngine()

    def fake_build_engine(_settings: Settings) -> Engine:
        return engine  # type: ignore[return-value]

    monkeypatch.setattr(database, "build_engine", fake_build_engine)

    result = database.database_status(settings())

    assert result.revision == "20260721_0001"
    assert result.timescaledb_version == "2.28.2"
    assert engine.disposed
