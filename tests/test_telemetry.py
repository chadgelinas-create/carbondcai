"""Telemetry persistence adapter unit tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import Engine, RowMapping
from sqlalchemy.exc import SQLAlchemyError

from skid2.application.ports import MeasurementStoreError
from skid2.contracts import DataQuality, Measurement
from skid2.persistence.telemetry import (
    DuplicateSampleConflictError,
    TelemetryRepository,
    row_to_measurement,
)

NOW = datetime(2026, 7, 27, 10, tzinfo=UTC)


def measurement(value: float = 10) -> Measurement:
    """Build one persistence-domain value."""

    return Measurement(
        point_id="synthetic.test",
        label="Synthetic Test Signal",
        source="synthetic.source",
        value=value,
        unit="test_unit",
        source_timestamp=NOW,
        receive_timestamp=NOW,
        quality=DataQuality.GOOD,
        synthetic=True,
    )


def row(value: float = 10, *, identifier: int = 1) -> dict[str, Any]:
    """Build one SQL-like mapping."""

    return {
        "id": identifier,
        "point_id": "synthetic.test",
        "label": "Synthetic Test Signal",
        "source": "synthetic.source",
        "value": value,
        "unit": "test_unit",
        "source_timestamp": NOW,
        "receive_timestamp": NOW,
        "quality": "good",
        "synthetic": True,
    }


def repository_connection(
    *,
    transaction: bool = False,
) -> tuple[TelemetryRepository, MagicMock]:
    """Create an engine context-manager double."""

    engine = MagicMock(spec=Engine)
    context = engine.begin.return_value if transaction else engine.connect.return_value
    connection = context.__enter__.return_value
    return TelemetryRepository(engine), connection


def test_row_mapping_preserves_every_domain_field() -> None:
    """Persistence mapping has no lossy conversion."""

    assert row_to_measurement(cast(RowMapping, row())) == measurement()


def test_append_insert_exact_duplicate_and_conflict() -> None:
    """Insert is idempotent and conflicting identities fail explicitly."""

    repository, connection = repository_connection(transaction=True)
    inserted = MagicMock()
    inserted.scalar_one_or_none.return_value = 1
    connection.execute.return_value = inserted
    assert repository.append(measurement())

    duplicate_repository, duplicate_connection = repository_connection(transaction=True)
    not_inserted = MagicMock()
    not_inserted.scalar_one_or_none.return_value = None
    existing = MagicMock()
    existing.mappings.return_value.one.return_value = row()
    duplicate_connection.execute.side_effect = [not_inserted, existing]
    assert not duplicate_repository.append(measurement())

    conflict_repository, conflict_connection = repository_connection(transaction=True)
    conflict_not_inserted = MagicMock()
    conflict_not_inserted.scalar_one_or_none.return_value = None
    conflict_existing = MagicMock()
    conflict_existing.mappings.return_value.one.return_value = row(value=99)
    conflict_connection.execute.side_effect = [conflict_not_inserted, conflict_existing]
    with pytest.raises(DuplicateSampleConflictError, match="different content"):
        conflict_repository.append(measurement())


def test_latest_history_and_count_have_bounded_results() -> None:
    """Read operations map values and enforce the public history limit."""

    latest_repository, latest_connection = repository_connection()
    latest_result = MagicMock()
    latest_result.mappings.return_value.first.return_value = row()
    latest_connection.execute.return_value = latest_result
    assert latest_repository.latest("synthetic.test") == measurement()

    missing_repository, missing_connection = repository_connection()
    missing_result = MagicMock()
    missing_result.mappings.return_value.first.return_value = None
    missing_connection.execute.return_value = missing_result
    assert missing_repository.latest("synthetic.test") is None

    history_repository, history_connection = repository_connection()
    history_result = MagicMock()
    history_result.mappings.return_value.all.return_value = [row(), row(identifier=2)]
    history_connection.execute.return_value = history_result
    assert history_repository.history("synthetic.test", 2) == (
        measurement(),
        measurement(),
    )
    with pytest.raises(ValueError, match="between 1 and 1000"):
        history_repository.history("synthetic.test", 0)

    count_repository, count_connection = repository_connection()
    count_result = MagicMock()
    count_result.scalar_one.return_value = 2
    count_connection.execute.return_value = count_result
    assert count_repository.count("synthetic.test") == 2


@pytest.mark.parametrize("method", ["append", "latest", "history", "count"])
def test_sql_failures_are_translated_to_application_boundary(method: str) -> None:
    """SQL diagnostics never need to cross into the application runtime."""

    repository, connection = repository_connection(transaction=method == "append")
    connection.execute.side_effect = SQLAlchemyError("internal SQL")

    with pytest.raises(MeasurementStoreError, match="telemetry"):
        if method == "append":
            repository.append(measurement())
        elif method == "latest":
            repository.latest("synthetic.test")
        elif method == "history":
            repository.history("synthetic.test")
        else:
            repository.count("synthetic.test")
