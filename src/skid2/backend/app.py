"""Version 1 FastAPI boundary for backend health and application APIs."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from skid2.application.devices import DeviceManager
from skid2.application.runtime import WalkingSkeletonRuntime
from skid2.application.simulation import SimulationService
from skid2.backend.auth import protected_access, simulation_access
from skid2.backend.auth import router as auth_router
from skid2.backend.configuration import router as configuration_router
from skid2.backend.ems_api import router as ems_router
from skid2.backend.modbus import ModbusGateway
from skid2.backend.monitor import router as monitor_router
from skid2.backend.schemas import ComponentHealthResponse, CurrentStateResponse
from skid2.backend.simulator import router as simulation_router
from skid2.contracts import ManualClock, SystemClock
from skid2.ems.auth import AuthService
from skid2.ems.charger import ChargerAvailability
from skid2.ems.charger_handshake import HandshakeMachine
from skid2.ems.charger_sessions import ChargerSessionStore
from skid2.ems.charger_yard import ChargerYard
from skid2.ems.config import ConfigService
from skid2.ems.device_manager import DeviceManager as EmsDeviceManager
from skid2.ems.hub import EmsHub
from skid2.ems.mppt import MpptController
from skid2.ems.setpoints import run_ems_poll
from skid2.ems.supervisor import Supervisor
from skid2.persistence.accounts import Accounts
from skid2.persistence.config import ConfigRepository
from skid2.persistence.configuration import ConfigurationConflictError, ConfigurationRepository
from skid2.persistence.database import build_engine, database_status
from skid2.persistence.simulation import SimulationRepository, SimulationStoreError
from skid2.persistence.telemetry import TelemetryRepository
from skid2.plant.profiles import INITIAL_AT
from skid2.protocol.registers import RegisterBank
from skid2.settings import get_settings

LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Own backend runtimes, database engine, and graceful shutdown."""

    settings = get_settings()
    engine = build_engine(settings)
    owner = engine.connect()
    if not owner.execute(text("SELECT pg_try_advisory_lock(731204)")).scalar_one():
        owner.close()
        engine.dispose()
        raise RuntimeError("Another backend already owns this plant. Run one backend process.")
    application.state.configuration = ConfigurationRepository(engine)
    application.state.configuration_lock = asyncio.Lock()
    runtime = WalkingSkeletonRuntime(
        settings=settings,
        clock=SystemClock(),
        repository=TelemetryRepository(engine),
    )
    application.state.accounts = Accounts(engine, settings.session_seconds)
    application.state.accounts.ensure_admin_users()
    if settings.environment == "development":
        application.state.accounts.ensure_dev_users()
    device_manager = DeviceManager(settings, ConfigRepository(engine), TelemetryRepository(engine))
    gateway: ModbusGateway | None = None
    simulation_stop = asyncio.Event()
    simulation_task: asyncio.Task[None] | None = None
    try:
        application.state.configuration.recover()
        if settings.monitor_enabled:
            application.state.device_manager = device_manager
            await device_manager.start()
        await runtime.start()
        application.state.walking_skeleton = runtime
        if settings.simulation_enabled:
            if settings.simulation_seed is None:
                raise ValueError("simulation requires an explicit seed")
            simulation = SimulationService(
                ManualClock(INITIAL_AT),
                timedelta(seconds=settings.simulation_step_seconds),
                settings.protocol_root,
                seed=settings.simulation_seed,
                profile_id=settings.simulation_profile,
                repository=SimulationRepository(engine),
            )
            try:
                await asyncio.to_thread(simulation.load_saved_state)
            except (SimulationStoreError, ValueError):
                LOGGER.warning(
                    "Saved simulation could not be restored; simulator remains unavailable"
                )
                application.state.simulation_error = (
                    "Saved simulation could not be restored. Saved data has been retained. "
                    "Check database availability and checkpoint compatibility, then restart the server."
                )
            else:
                application.state.simulation = simulation
                simulation_task = asyncio.create_task(
                    _run_simulation(simulation, simulation_stop),
                    name="skid2-simulation",
                )
                if settings.modbus_enabled:
                    bank = RegisterBank.load(settings.modbus_bank_path, simulation.values)
                    gateway = ModbusGateway(settings, bank)
                    await gateway.start()
                    application.state.modbus_gateway = gateway
        if settings.ems_enabled:
            hub = EmsHub()
            config = ConfigService(engine)
            auth = AuthService(engine, settings.auth_secret.get_secret_value())
            telemetry = TelemetryRepository(engine)
            try:
                auth.ensure_seed_users()
                config.ensure_defaults()
            except Exception:
                LOGGER.exception("EMS seed initialization failed")
            application.state.ems_hub = hub
            application.state.ems_config = config
            application.state.ems_auth = auth
            application.state.ems_telemetry = telemetry
            application.state.ems_charger = ChargerAvailability(config)
            application.state.ems_charger_yard = ChargerYard()
            application.state.ems_supervisor = Supervisor(hub, config, engine)
            sessions = ChargerSessionStore(engine)
            sessions.restore_into(application.state.ems_charger_yard)
            application.state.ems_charger_sessions = sessions
            application.state.ems_handshake = HandshakeMachine()
            application.state.ems_mppt = MpptController()
            ems_manager = EmsDeviceManager(
                hub,
                telemetry,
                data_source=settings.data_source,
                poll_seconds=settings.ems_poll_seconds,
            )
            ems_manager.attach_yard(application.state.ems_charger_yard)
            simulation_state = getattr(application.state, "simulation", None)
            if simulation_state is not None:
                ems_manager.attach_simulation(simulation_state.values)

            def _ems_poll() -> None:
                run_ems_poll(
                    hub=hub,
                    config=config,
                    charger=application.state.ems_charger,
                    supervisor=application.state.ems_supervisor,
                    yard=application.state.ems_charger_yard,
                    sessions=application.state.ems_charger_sessions,
                    handshake=application.state.ems_handshake,
                    mppt=application.state.ems_mppt,
                    simulation=getattr(application.state, "simulation", None),
                    dt_seconds=settings.ems_poll_seconds,
                )

            ems_manager.attach_poll_hook(_ems_poll)
            application.state.ems_device_manager = ems_manager
            await ems_manager.start()
        yield
    finally:
        ems_manager = getattr(application.state, "ems_device_manager", None)
        if ems_manager is not None:
            await ems_manager.stop()
        await device_manager.stop()
        if hasattr(application.state, "device_manager"):
            del application.state.device_manager
        del application.state.accounts
        simulation_stop.set()
        if simulation_task is not None:
            await simulation_task
        # Stop network requests before removing their simulator value source.
        if gateway is not None:
            await gateway.stop()
        if hasattr(application.state, "modbus_gateway"):
            del application.state.modbus_gateway
        if hasattr(application.state, "simulation"):
            del application.state.simulation
        if hasattr(application.state, "simulation_error"):
            del application.state.simulation_error
        for attr in (
            "ems_device_manager",
            "ems_hub",
            "ems_config",
            "ems_auth",
            "ems_telemetry",
            "ems_charger",
            "ems_charger_yard",
            "ems_charger_sessions",
            "ems_handshake",
            "ems_mppt",
            "ems_supervisor",
        ):
            if hasattr(application.state, attr):
                delattr(application.state, attr)
        await runtime.stop()
        del application.state.configuration
        del application.state.configuration_lock
        owner.execute(text("SELECT pg_advisory_unlock(731204)"))
        owner.close()
        engine.dispose()


async def _run_simulation(
    simulation: SimulationService, stop: asyncio.Event, interval: float | None = None
) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(
                stop.wait(), timeout=simulation.step_seconds if interval is None else interval
            )
        except TimeoutError:
            await asyncio.to_thread(simulation.tick)


app = FastAPI(
    title="Carbon DC Skid 2",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


@app.exception_handler(SimulationStoreError)
async def simulation_store_error(_request: Request, error: SimulationStoreError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(error)})


_DEFAULT_CORS_ORIGINS = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:4173",
    "http://localhost:4173",
    "https://carbon-dc-ems-ui-demo.netlify.app",
    "https://carbon-dc-ems-ui.netlify.app",
    "https://carbondcai-ems.pages.dev",
]
_extra_cors = [
    origin.strip()
    for origin in os.environ.get("SKID2_CORS_ORIGINS", "").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=[*_DEFAULT_CORS_ORIGINS, *_extra_cors],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(configuration_router)
app.include_router(monitor_router)
app.include_router(ems_router)
app.include_router(simulation_router, dependencies=[Depends(simulation_access)])


@app.exception_handler(ConfigurationConflictError)
async def configuration_conflict(
    _request: Request, error: ConfigurationConflictError
) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(error)})


@app.exception_handler(SQLAlchemyError)
async def storage_unavailable(_request: Request, _error: SQLAlchemyError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": "The change could not be confirmed. Reload settings to check its outcome."
        },
    )


class LiveResponse(BaseModel):
    """Liveness response."""

    status: Literal["alive"]


class ReadyResponse(BaseModel):
    """Database-backed readiness response."""

    status: Literal["ready"]
    database_revision: str
    timescaledb_version: str


def _runtime(request: Request) -> WalkingSkeletonRuntime:
    """Return initialized runtime or a bounded readiness failure."""

    runtime = getattr(request.app.state, "walking_skeleton", None)
    if not isinstance(runtime, WalkingSkeletonRuntime):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="walking-skeleton runtime is not ready",
        )
    return runtime


@app.get("/api/v1/health/live", response_model=LiveResponse)
def live() -> LiveResponse:
    """Report that the Python process can serve requests."""

    return LiveResponse(status="alive")


@app.get("/api/v1/health/ready", response_model=ReadyResponse)
def ready() -> ReadyResponse:
    """Report ready only after settings, database, extension, and migration are valid."""

    try:
        current = database_status(get_settings())
    except (OSError, SQLAlchemyError, ValueError):
        LOGGER.warning("Database readiness check failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="backend database is not ready",
        ) from None

    return ReadyResponse(
        status="ready",
        database_revision=current.revision,
        timescaledb_version=current.timescaledb_version,
    )


@app.get(
    "/api/v1/current-state",
    response_model=CurrentStateResponse,
    dependencies=[Depends(protected_access)],
)
def current_state(request: Request) -> CurrentStateResponse:
    """Return one typed snapshot with explicit freshness and synthetic identity."""

    return CurrentStateResponse.from_domain(_runtime(request).snapshot())


@app.get("/api/v1/health/components", response_model=ComponentHealthResponse)
def component_health(request: Request) -> ComponentHealthResponse:
    """Distinguish database, migration, source, state, and historian health."""

    try:
        database_status(get_settings())
    except (OSError, SQLAlchemyError, ValueError):
        LOGGER.warning("Component database health check failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="component health is not ready",
        ) from None
    return ComponentHealthResponse.from_runtime(_runtime(request).health())
