"""Small database checks used by migrations, readiness, and Phase 1 evidence."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, create_engine, text

from skid2.settings import Settings


@dataclass(frozen=True, slots=True)
class DatabaseStatus:
    """Successful database connectivity and migration status."""

    revision: str
    timescaledb_version: str


def build_engine(settings: Settings) -> Engine:
    """Build a short-lived synchronous engine without logging credentials."""

    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        hide_parameters=True,
    )


def database_status(settings: Settings) -> DatabaseStatus:
    """Prove connectivity, current Alembic revision, and TimescaleDB availability."""

    engine = build_engine(settings)
    try:
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            extension_version = connection.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
            ).scalar_one()
    finally:
        engine.dispose()

    return DatabaseStatus(revision=str(revision), timescaledb_version=str(extension_version))
