"""Executable correlation check for the Phase 2 API and historian path."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from http.cookiejar import CookieJar
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from skid2.application.ports import MeasurementStoreError
from skid2.backend.schemas import CurrentStateResponse, MeasurementResponse
from skid2.contracts import Freshness
from skid2.persistence.database import build_engine
from skid2.persistence.telemetry import TelemetryRepository
from skid2.settings import get_settings
from skid2.sim.synthetic import SYNTHETIC_POINT_ID

ALLOWED_INTERNAL_HOSTS = frozenset({"127.0.0.1", "localhost", "backend", "frontend"})


class VerificationError(RuntimeError):
    """A walking-skeleton boundary did not preserve the expected contract."""


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Non-secret evidence emitted by a successful correlation check."""

    point_id: str
    freshness: str
    value: float
    unit: str
    source_timestamp: str
    historian_samples: int


def fetch_snapshot(api_url: str) -> CurrentStateResponse:
    """Fetch and validate one current-state response from an internal endpoint."""

    parsed = urlsplit(api_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in ALLOWED_INTERNAL_HOSTS
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise VerificationError("API URL must be an uncredentialed internal HTTP endpoint")
    username = os.environ.get("SKID2_VERIFY_USERNAME")
    password = os.environ.get("SKID2_VERIFY_PASSWORD")
    if username and password:
        opener = build_opener(HTTPCookieProcessor(CookieJar()))
        origin = get_settings().browser_origin
        login = Request(
            f"http://{parsed.netloc}/api/v1/login",
            data=json.dumps({"username": username, "password": password}).encode(),
            headers={"Origin": origin, "Content-Type": "application/json"},
        )
        with opener.open(login, timeout=10) as response:
            response.read()
        with opener.open(api_url, timeout=3) as response:
            payload = json.loads(response.read())
    else:
        with urlopen(api_url, timeout=3) as response:  # noqa: S310 - URL constrained above.
            payload = json.loads(response.read())
    if isinstance(payload, dict) and "state" in payload:
        payload = payload["state"]
    return CurrentStateResponse.model_validate(payload)


def correlate(
    snapshot: CurrentStateResponse,
    latest: MeasurementResponse | None,
    sample_count: int,
    expected_freshness: Freshness,
    minimum_samples: int,
    expected_point_id: str = SYNTHETIC_POINT_ID,
) -> VerificationResult:
    """Prove API and historian identity/values match without a false zero."""

    if snapshot.freshness is not expected_freshness:
        raise VerificationError(
            f"expected freshness {expected_freshness.value}, got {snapshot.freshness.value}"
        )
    if snapshot.measurement is None:
        raise VerificationError("current-state response has no measurement")
    if latest is None:
        raise VerificationError("historian has no matching measurement")
    if snapshot.measurement != latest:
        raise VerificationError("API measurement does not match latest historian row")
    if snapshot.measurement.point_id != expected_point_id:
        raise VerificationError("point identity does not match the selected verification route")
    if not snapshot.measurement.synthetic:
        raise VerificationError("walking-skeleton measurement is not marked synthetic")
    if sample_count < minimum_samples:
        raise VerificationError(
            f"expected at least {minimum_samples} historian samples, got {sample_count}"
        )
    return VerificationResult(
        point_id=snapshot.measurement.point_id,
        freshness=snapshot.freshness.value,
        value=snapshot.measurement.value,
        unit=snapshot.measurement.unit,
        source_timestamp=snapshot.measurement.source_timestamp.isoformat(),
        historian_samples=sample_count,
    )


def build_parser() -> argparse.ArgumentParser:
    """Create the intentionally small verification CLI."""

    parser = argparse.ArgumentParser(prog="skid2-walking-skeleton-verify")
    parser.add_argument(
        "--api-url",
        default="http://127.0.0.1:8000/api/v1/monitor",
    )
    parser.add_argument(
        "--expected-freshness",
        choices=tuple(item.value for item in Freshness),
        default=Freshness.LIVE.value,
    )
    parser.add_argument("--minimum-samples", type=int, default=1)
    return parser


def run(api_url: str, expected_freshness: Freshness, minimum_samples: int) -> int:
    """Run the correlation against the configured TimescaleDB."""

    if minimum_samples < 1:
        raise VerificationError("minimum sample count must be positive")
    snapshot = fetch_snapshot(api_url)
    engine = build_engine(get_settings())
    try:
        repository = TelemetryRepository(engine)
        point_id = (
            "bess.cluster.soc"
            if urlsplit(api_url).path.endswith("/monitor")
            else SYNTHETIC_POINT_ID
        )
        item = snapshot.measurement
        if item is None:
            raise VerificationError("current-state response has no measurement")
        latest = repository.find(point_id, item.source, item.source_timestamp)
        latest_response = None if latest is None else MeasurementResponse.from_domain(latest)
        result = correlate(
            snapshot,
            latest_response,
            repository.count(point_id),
            expected_freshness,
            minimum_samples,
            expected_point_id=point_id,
        )
    finally:
        engine.dispose()
    sys.stdout.write(f"{json.dumps(asdict(result), sort_keys=True)}\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Return a process status with a bounded diagnostic on failure."""

    arguments = build_parser().parse_args(argv)
    try:
        return run(
            api_url=str(arguments.api_url),
            expected_freshness=Freshness(str(arguments.expected_freshness)),
            minimum_samples=int(arguments.minimum_samples),
        )
    except (MeasurementStoreError, OSError, ValueError, VerificationError) as error:
        sys.stderr.write(f"walking-skeleton verification failed: {error}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
