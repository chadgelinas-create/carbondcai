"""Executable vertical-path correlation tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Self

import pytest

from skid2.backend.schemas import CurrentStateResponse, MeasurementResponse
from skid2.contracts import DataQuality, Freshness, Measurement
from skid2.verification import (
    VerificationError,
    correlate,
    fetch_snapshot,
    main,
    run,
)

NOW = datetime(2026, 7, 27, 10, tzinfo=UTC)


def domain_measurement(value: float = 10) -> Measurement:
    """Build the approved synthetic sample."""

    return Measurement(
        point_id="synthetic.walking_skeleton.test_signal_001",
        label="Synthetic Walking-Skeleton Test Signal",
        source="synthetic.phase2.generator",
        value=value,
        unit="test_unit",
        source_timestamp=NOW,
        receive_timestamp=NOW,
        quality=DataQuality.GOOD,
        synthetic=True,
    )


def snapshot(
    *,
    freshness: Freshness = Freshness.LIVE,
    item: Measurement | None = None,
) -> CurrentStateResponse:
    """Build one typed API snapshot."""

    selected = domain_measurement() if item is None else item
    return CurrentStateResponse(
        observed_at=NOW,
        freshness=freshness,
        measurement=MeasurementResponse.from_domain(selected),
        heartbeat=None,
    )


def test_correlation_returns_safe_evidence_for_exact_match() -> None:
    """A successful result records identity and count without infrastructure details."""

    response = snapshot()
    result = correlate(
        response,
        response.measurement,
        sample_count=2,
        expected_freshness=Freshness.LIVE,
        minimum_samples=2,
    )

    assert result.point_id == "synthetic.walking_skeleton.test_signal_001"
    assert result.value == 10
    assert result.historian_samples == 2


def test_correlation_rejects_every_material_mismatch() -> None:
    """Freshness, presence, content, identity, origin, and count must all agree."""

    valid = snapshot()
    with pytest.raises(VerificationError, match="expected freshness"):
        correlate(valid, valid.measurement, 1, Freshness.STALE, 1)
    with pytest.raises(VerificationError, match="no measurement"):
        correlate(
            CurrentStateResponse(
                observed_at=NOW,
                freshness=Freshness.UNAVAILABLE,
                measurement=None,
                heartbeat=None,
            ),
            valid.measurement,
            1,
            Freshness.UNAVAILABLE,
            1,
        )
    with pytest.raises(VerificationError, match="historian has no"):
        correlate(valid, None, 1, Freshness.LIVE, 1)
    with pytest.raises(VerificationError, match="does not match"):
        correlate(
            valid,
            MeasurementResponse.from_domain(domain_measurement(11)),
            1,
            Freshness.LIVE,
            1,
        )
    wrong_identity = domain_measurement()
    object.__setattr__(wrong_identity, "point_id", "synthetic.other")
    wrong_snapshot = snapshot(item=wrong_identity)
    with pytest.raises(VerificationError, match="approved synthetic ID"):
        correlate(
            wrong_snapshot,
            wrong_snapshot.measurement,
            1,
            Freshness.LIVE,
            1,
        )
    non_synthetic = domain_measurement()
    object.__setattr__(non_synthetic, "synthetic", False)
    non_synthetic_snapshot = snapshot(item=non_synthetic)
    with pytest.raises(VerificationError, match="not marked synthetic"):
        correlate(
            non_synthetic_snapshot,
            non_synthetic_snapshot.measurement,
            1,
            Freshness.LIVE,
            1,
        )
    with pytest.raises(VerificationError, match="at least 2"):
        correlate(valid, valid.measurement, 1, Freshness.LIVE, 2)


class ResponseContext:
    """Minimal urllib response context."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def test_fetch_snapshot_restricts_url_and_validates_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The evidence utility never fetches arbitrary or credentialed URLs."""

    payload = snapshot().model_dump(mode="json")
    monkeypatch.setattr(
        "skid2.verification.urlopen",
        lambda *_args, **_kwargs: ResponseContext(json.dumps(payload).encode()),
    )

    observed = fetch_snapshot("http://backend:8000/api/v1/current-state")

    assert observed == snapshot()
    with pytest.raises(VerificationError, match="internal HTTP"):
        fetch_snapshot("https://example.com/api")
    with pytest.raises(VerificationError, match="uncredentialed"):
        fetch_snapshot("http://user:password@backend:8000/api")  # pragma: allowlist secret


def test_run_queries_repository_and_emits_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The executable check correlates API and durable state."""

    item = domain_measurement()

    class FakeEngine:
        disposed = False

        def dispose(self) -> None:
            self.disposed = True

    class FakeRepository:
        def __init__(self, _engine: object) -> None:
            pass

        def latest(self, _point_id: str) -> Measurement:
            return item

        def count(self, _point_id: str) -> int:
            return 2

    engine = FakeEngine()
    monkeypatch.setattr("skid2.verification.fetch_snapshot", lambda _url: snapshot())
    monkeypatch.setattr("skid2.verification.build_engine", lambda _settings: engine)
    monkeypatch.setattr("skid2.verification.get_settings", lambda: object())
    monkeypatch.setattr("skid2.verification.TelemetryRepository", FakeRepository)

    assert run("http://backend/api", Freshness.LIVE, 2) == 0
    assert engine.disposed
    assert json.loads(capsys.readouterr().out)["historian_samples"] == 2

    with pytest.raises(VerificationError, match="positive"):
        run("http://backend/api", Freshness.LIVE, 0)


def test_main_returns_bounded_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI failures return nonzero without a traceback."""

    assert main(["--api-url", "https://example.com/api"]) == 1
    assert "verification failed" in capsys.readouterr().err
