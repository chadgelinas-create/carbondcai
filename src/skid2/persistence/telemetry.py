"""Explicit domain-to-TimescaleDB boundary for generic telemetry samples."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import cast

from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError

from skid2.application.ports import MeasurementConflictError, MeasurementStoreError
from skid2.contracts import DataQuality, Measurement
from skid2.persistence.metadata import telemetry_sample


class DuplicateSampleConflictError(MeasurementConflictError):
    """Raised when an existing sample identity has different content."""


def row_to_measurement(row: RowMapping) -> Measurement:
    """Map one SQLAlchemy row to the framework-neutral domain contract."""

    return Measurement(
        point_id=str(row["point_id"]),
        label=str(row["label"]),
        source=str(row["source"]),
        value=float(row["value"]),
        unit=str(row["unit"]),
        source_timestamp=cast(datetime, row["source_timestamp"]),
        receive_timestamp=cast(datetime, row["receive_timestamp"]),
        quality=DataQuality(str(row["quality"])),
        synthetic=bool(row["synthetic"]),
    )


class TelemetryRepository:
    """Store and query measurements through one generic historian path."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append(self, measurement: Measurement) -> bool:
        """Insert once, return false for an exact duplicate, reject conflicts."""

        values = {
            "point_id": measurement.point_id,
            "label": measurement.label,
            "source": measurement.source,
            "value": measurement.value,
            "unit": measurement.unit,
            "source_timestamp": measurement.source_timestamp,
            "receive_timestamp": measurement.receive_timestamp,
            "quality": measurement.quality.value,
            "synthetic": measurement.synthetic,
        }
        statement = (
            insert(telemetry_sample)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["point_id", "source", "source_timestamp"])
            .returning(telemetry_sample.c.id)
        )
        identity_filter = (
            (telemetry_sample.c.point_id == measurement.point_id)
            & (telemetry_sample.c.source == measurement.source)
            & (telemetry_sample.c.source_timestamp == measurement.source_timestamp)
        )
        try:
            with self._engine.begin() as connection:
                inserted_id = connection.execute(statement).scalar_one_or_none()
                if inserted_id is not None:
                    return True
                existing = (
                    connection.execute(select(telemetry_sample).where(identity_filter))
                    .mappings()
                    .one()
                )
        except SQLAlchemyError as error:
            raise MeasurementStoreError("could not persist telemetry sample") from error
        if row_to_measurement(existing) != measurement:
            raise DuplicateSampleConflictError(
                "sample identity already exists with different content"
            )
        return False

    def latest(self, point_id: str) -> Measurement | None:
        """Return the newest sample with stable tie-breaking."""

        statement = (
            select(telemetry_sample)
            .where(telemetry_sample.c.point_id == point_id)
            .order_by(
                telemetry_sample.c.source_timestamp.desc(),
                telemetry_sample.c.receive_timestamp.desc(),
                telemetry_sample.c.id.desc(),
            )
            .limit(1)
        )
        try:
            with self._engine.connect() as connection:
                row = connection.execute(statement).mappings().first()
        except SQLAlchemyError as error:
            raise MeasurementStoreError("could not read latest telemetry sample") from error
        return None if row is None else row_to_measurement(row)

    def find(self, point_id: str, source: str, at: datetime) -> Measurement | None:
        """Fetch the exact published identity; later samples cannot race this check."""
        try:
            with self._engine.connect() as connection:
                row = (
                    connection.execute(
                        select(telemetry_sample).where(
                            telemetry_sample.c.point_id == point_id,
                            telemetry_sample.c.source == source,
                            telemetry_sample.c.source_timestamp == at,
                        )
                    )
                    .mappings()
                    .first()
                )
        except SQLAlchemyError as error:
            raise MeasurementStoreError("could not read the published telemetry sample") from error
        return None if row is None else row_to_measurement(row)

    def history(self, point_id: str, limit: int = 100) -> Sequence[Measurement]:
        """Return samples in deterministic chronological order."""

        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        statement = (
            select(telemetry_sample)
            .where(telemetry_sample.c.point_id == point_id)
            .order_by(
                telemetry_sample.c.source_timestamp,
                telemetry_sample.c.receive_timestamp,
                telemetry_sample.c.id,
            )
            .limit(limit)
        )
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(statement).mappings().all()
        except SQLAlchemyError as error:
            raise MeasurementStoreError("could not read telemetry history") from error
        return tuple(row_to_measurement(row) for row in rows)

    def count(self, point_id: str) -> int:
        """Count persisted samples for verification/evidence."""

        statement = (
            select(func.count())
            .select_from(telemetry_sample)
            .where(telemetry_sample.c.point_id == point_id)
        )
        try:
            with self._engine.connect() as connection:
                return int(connection.execute(statement).scalar_one())
        except SQLAlchemyError as error:
            raise MeasurementStoreError("could not count telemetry samples") from error
