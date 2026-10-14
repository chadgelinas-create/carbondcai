"""Shared test-runtime configuration."""

from collections.abc import Iterator

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Use the standard-library asyncio backend for ASGI tests."""

    return "asyncio"


@pytest.fixture
def legacy_api() -> Iterator[None]:
    """Keep existing simulation tests independent of account storage."""
    from skid2.backend.app import app
    from skid2.backend.auth import protected_access, simulation_access

    app.dependency_overrides[simulation_access] = lambda: None
    app.dependency_overrides[protected_access] = lambda: None
    yield
    app.dependency_overrides.pop(simulation_access, None)
    app.dependency_overrides.pop(protected_access, None)
