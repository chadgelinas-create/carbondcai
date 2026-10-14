"""Simulation routes served by the backend."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Literal, Self

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from skid2.application.simulation import (
    BusSample,
    DeviceSummary,
    PvSample,
    SimulationActivity,
    SimulationInputs,
    SimulationService,
    SimulationState,
    StabilitiFleetSummary,
)
from skid2.application.simulation_checkpoint import SavedRunInfo
from skid2.backend.action_audit import AuditedActionRoute
from skid2.backend.modbus import (
    GatewayRoute,
    GatewayStatus,
    ModbusGateway,
    stopped_gateway_status,
)
from skid2.plant.battery import BatteryConfig
from skid2.plant.bus import BusConfig
from skid2.plant.model import PlantState
from skid2.plant.pv import PROFILE_IDS
from skid2.settings import get_settings
from skid2.sim.battery import BatteryInputs
from skid2.sim.converter import ConverterInputs
from skid2.sim.dc600 import Dc600Inputs
from skid2.sim.device import DeviceCapability, DeviceFault, DeviceValue
from skid2.sim.faults import (
    FaultRequest,
    FaultRule,
    FaultTest,
    FaultType,
    fault_state,
    find_rule,
)
from skid2.sim.runtime import FrameFailure
from skid2.sim.stabiliti import StabilitiInputs

router = APIRouter(route_class=AuditedActionRoute)


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BatteryInputRequest(StrictRequest):
    kind: Literal["battery"]
    cluster_soc: float = Field(ge=0, le=1)
    voltage: float = Field(ge=0, le=6553.5)
    current: float = Field(ge=-3276.8, le=3276.7)
    maximum_temperature: float = Field(ge=-40, le=125)
    charge_power_limit: float = Field(ge=0, le=65535)
    discharge_power_limit: float = Field(ge=0, le=65535)
    main_contactor: bool
    precharge_contactor: bool
    precharge_state: int = Field(ge=0, le=4)
    fire_communication_state: int = Field(ge=0, le=2)
    communication_ok: bool
    fault_active: bool

    def to_domain(self) -> BatteryInputs:
        return BatteryInputs(**self.model_dump(exclude={"kind"}))


class ConverterInputRequest(StrictRequest):
    kind: Literal["converter"]
    enabled: bool
    requested_power: float = Field(ge=-3276.8, le=3276.7)
    available_power: float = Field(ge=0, le=6553.5)
    charge_power_limit: float = Field(ge=0, le=6553.5)
    discharge_power_limit: float = Field(ge=0, le=6553.5)
    dc_voltage: float = Field(ge=0, le=6553.5)
    dc_current: float = Field(ge=-3276.8, le=3276.7)
    temperature: float = Field(ge=-32768, le=32767)
    fault_active: bool
    communication_ok: bool
    pv_available: bool
    mppt_applying: bool
    test_ramp_per_step: float | None = Field(default=None, ge=0)
    test_derate_limit: float | None = Field(default=None, ge=0)

    def to_domain(self) -> ConverterInputs:
        return ConverterInputs(**self.model_dump(exclude={"kind"}))


class StabilitiInputRequest(StrictRequest):
    kind: Literal["stabiliti"]
    enabled: bool
    available_power: float = Field(ge=0)
    actual_power: float
    bus_load: float | None = Field(default=None, ge=0, strict=True)
    power_limit: float = Field(ge=0)
    maximum_temperature: float
    derated: bool
    communication_ok: bool
    fault_active: bool

    def to_domain(self) -> StabilitiInputs:
        return StabilitiInputs(**self.model_dump(exclude={"kind"}))


class Dc600InputRequest(StrictRequest):
    kind: Literal["dc600"]
    demand_power: float | None = Field(default=None, ge=0)
    available_power: float | None = Field(default=None, ge=0)
    actual_power: float | None = Field(default=None, ge=0)
    demand_fresh: bool
    available_fresh: bool
    session_active: bool
    communication_ok: bool
    fault_active: bool

    def to_domain(self) -> Dc600Inputs:
        return Dc600Inputs(**self.model_dump(exclude={"kind"}))


InputRequest = Annotated[
    BatteryInputRequest | ConverterInputRequest | StabilitiInputRequest | Dc600InputRequest,
    Field(discriminator="kind"),
]


class CapabilityResponse(BaseModel):
    """Evidence-aware device capability response."""

    capability_id: str
    kind: str
    evidence: str
    fidelity: str
    review_status: str
    available: bool
    enabled_profiles: tuple[str, ...]
    source_ids: tuple[str, ...]
    decision_ids: tuple[str, ...]
    assumptions: tuple[str, ...]

    @classmethod
    def from_domain(cls, capability: DeviceCapability) -> Self:
        return cls(
            capability_id=capability.capability_id,
            kind=capability.kind.value,
            evidence=capability.evidence_classification.value,
            fidelity=capability.fidelity_level.value,
            review_status=capability.review_status,
            available=capability.available,
            enabled_profiles=tuple(
                item.value for item in sorted(capability.enabled_profiles, key=str)
            ),
            source_ids=capability.source_ids,
            decision_ids=capability.decision_ids,
            assumptions=capability.assumptions,
        )


class DeviceValueResponse(BaseModel):
    logical_id: str
    value: bool | int | float | str
    unit: str
    quality: str
    source: str
    provisional: bool
    test_id: str | None
    test_started_at: datetime | None

    @classmethod
    def from_domain(cls, value: DeviceValue) -> Self:
        return cls(
            logical_id=value.logical_id,
            value=value.value,
            unit=value.unit,
            quality=value.quality.value,
            source=value.source,
            provisional=value.provisional,
            test_id=value.test_id,
            test_started_at=value.test_started_at,
        )


class DeviceFaultResponse(BaseModel):
    fault_id: str
    active: bool
    severity: str
    source: str
    test_id: str | None
    test_started_at: datetime | None

    @classmethod
    def from_domain(cls, fault: DeviceFault) -> Self:
        return cls(
            fault_id=fault.fault_id,
            active=fault.active,
            severity=fault.severity.value,
            source=fault.source,
            test_id=fault.test_id,
            test_started_at=fault.test_started_at,
        )


class DeviceResponse(BaseModel):
    """Controlled device inventory response."""

    device_id: str
    display_name: str
    role: str
    lifecycle: str
    health: str
    test_id: str | None
    test_started_at: datetime | None
    implementation_status: str
    protocol_status: str
    protocol: str
    endpoint_status: str
    model_status: str
    firmware_status: str
    blocker_reason: str | None
    capabilities: tuple[CapabilityResponse, ...]
    values: tuple[DeviceValueResponse, ...]
    faults: tuple[DeviceFaultResponse, ...]
    inputs: InputRequest
    applied_inputs: InputRequest
    inputs_pending: bool
    status_text: str
    testing_method: str
    direct_connection: str
    test_notice: str | None

    @classmethod
    def from_domain(cls, device: DeviceSummary) -> Self:
        return cls(
            device_id=device.device_id,
            display_name=device.display_name,
            role=device.role,
            lifecycle=device.lifecycle,
            health=device.health.value,
            test_id=device.test_id,
            test_started_at=device.test_started_at,
            implementation_status=device.implementation_status,
            protocol_status=device.protocol_status,
            protocol=device.protocol,
            endpoint_status=device.endpoint_status,
            model_status=device.model_status,
            firmware_status=device.firmware_status,
            blocker_reason=device.blocker_reason,
            capabilities=tuple(
                CapabilityResponse.from_domain(item) for item in device.capabilities
            ),
            values=tuple(DeviceValueResponse.from_domain(item) for item in device.values),
            faults=tuple(DeviceFaultResponse.from_domain(item) for item in device.faults),
            inputs=_input_response(device.inputs),
            applied_inputs=_input_response(device.applied_inputs),
            inputs_pending=device.inputs_pending,
            status_text=device.status_text,
            testing_method=device.testing_method,
            direct_connection=device.direct_connection,
            test_notice=device.test_notice,
        )


class ActivityResponse(BaseModel):
    sequence: int
    at: datetime
    device_id: str
    category: str
    message: str
    test_id: str | None
    test_started_at: datetime | None

    @classmethod
    def from_domain(cls, activity: SimulationActivity) -> Self:
        return cls(
            sequence=activity.sequence,
            at=activity.at,
            device_id=activity.device_id,
            category=activity.category,
            message=activity.message,
            test_id=activity.test_id,
            test_started_at=activity.test_started_at,
        )


class FaultRuleResponse(BaseModel):
    fault_type: str
    label: str
    device_ids: tuple[str, ...]
    needs_point: bool
    needs_value: bool
    latched: bool
    expected_result: str
    clear_method: str
    fidelity: str
    evidence: str

    @classmethod
    def from_domain(cls, rule: FaultRule) -> Self:
        return cls(
            fault_type=rule.fault_type.value,
            label=rule.label,
            device_ids=rule.devices,
            needs_point=rule.needs_point,
            needs_value=rule.needs_value,
            latched=rule.latched,
            expected_result=rule.expected_result,
            clear_method=rule.clear_method,
            fidelity=rule.fidelity,
            evidence=rule.evidence,
        )


class FaultTestResponse(BaseModel):
    test_id: str
    device_id: str
    fault_type: str
    label: str
    point_id: str | None
    test_value: float | None
    start_step: int
    duration_steps: int | None
    started_at: datetime
    cleared_at: datetime | None
    state: str
    latched: bool
    expected_result: str
    clear_method: str

    @classmethod
    def from_domain(cls, test: FaultTest, sequence: int) -> Self:
        rule = find_rule(test.device_id, test.fault_type)
        return cls(
            test_id=test.test_id,
            device_id=test.device_id,
            fault_type=test.fault_type.value,
            label=rule.label,
            point_id=test.point_id,
            test_value=test.test_value,
            start_step=test.start_step,
            duration_steps=test.duration_steps,
            started_at=test.started_at,
            cleared_at=test.cleared_at,
            state=fault_state(test, sequence, rule).value,
            latched=rule.latched,
            expected_result=rule.expected_result,
            clear_method=rule.clear_method,
        )


class FaultCatalogResponse(BaseModel):
    rules: tuple[FaultRuleResponse, ...]
    tests: tuple[FaultTestResponse, ...]


class StabilitiFleetResponse(BaseModel):
    ready: int
    available: int
    derated: int
    faulted: int
    total: int
    available_power: float

    @classmethod
    def from_domain(cls, fleet: StabilitiFleetSummary) -> Self:
        return cls(
            ready=fleet.ready,
            available=fleet.available,
            derated=fleet.derated,
            faulted=fleet.faulted,
            total=fleet.total,
            available_power=fleet.available_power,
        )


class GatewayRouteResponse(BaseModel):
    """One Modbus device route available to simulator clients."""

    device_id: str
    modbus_id: int
    functions: tuple[int, ...]
    registers: tuple[RegisterDescriptionResponse, ...]

    @classmethod
    def from_domain(cls, route: GatewayRoute) -> Self:
        return cls(
            device_id=route.device_id,
            modbus_id=route.modbus_id,
            functions=route.functions,
            registers=tuple(
                RegisterDescriptionResponse(
                    logical_id=item.logical_id,
                    function=item.function,
                    address=item.address,
                    display=item.display,
                    count=item.count,
                    unit=item.unit,
                )
                for item in route.registers
            ),
        )


class RegisterDescriptionResponse(BaseModel):
    logical_id: str
    function: int
    address: int
    display: str
    count: int
    unit: str


class GatewayResponse(BaseModel):
    """Live Modbus TCP connection status."""

    state: str
    host: str
    port: int
    clients: int
    last_request: str | None
    routes: tuple[GatewayRouteResponse, ...]

    @classmethod
    def from_domain(cls, gateway: GatewayStatus) -> Self:
        return cls(
            state=gateway.state,
            host=gateway.host,
            port=gateway.port,
            clients=gateway.clients,
            last_request=gateway.last_request,
            routes=tuple(GatewayRouteResponse.from_domain(item) for item in gateway.routes),
        )


class SimulationResponse(BaseModel):
    """Typed simulation state response."""

    configuration_generation: int = 0
    plant: PlantState
    battery_history: tuple[BusSample, ...]
    bus_history: tuple[BusSample, ...]
    pv_history: tuple[PvSample, ...]
    plant_trace_hash: str
    failure: FrameFailure | None
    snapshot_saved: bool
    saved_run: SavedRunInfo | None
    restored_run: SavedRunInfo | None
    lifecycle: str
    at: datetime
    sequence: int
    trace_entries: int
    trace_hash: str
    devices: tuple[DeviceResponse, ...]
    activity: tuple[ActivityResponse, ...]
    stabiliti_fleet: StabilitiFleetResponse
    gateway: GatewayResponse
    fault_rules: tuple[FaultRuleResponse, ...]
    fault_tests: tuple[FaultTestResponse, ...]

    @classmethod
    def from_domain(cls, state: SimulationState, gateway: GatewayStatus) -> Self:
        return cls(
            plant=state.plant,
            battery_history=state.battery_history,
            bus_history=state.bus_history,
            pv_history=state.pv_history,
            plant_trace_hash=state.plant_trace_hash,
            failure=state.failure,
            snapshot_saved=state.snapshot_saved,
            saved_run=state.saved_run,
            restored_run=state.restored_run,
            lifecycle=state.lifecycle.value,
            at=state.at,
            sequence=state.sequence,
            trace_entries=state.trace_entries,
            trace_hash=state.trace_hash,
            devices=tuple(DeviceResponse.from_domain(item) for item in state.devices),
            activity=tuple(ActivityResponse.from_domain(item) for item in state.activity),
            stabiliti_fleet=StabilitiFleetResponse.from_domain(state.stabiliti_fleet),
            gateway=GatewayResponse.from_domain(gateway),
            fault_rules=tuple(FaultRuleResponse.from_domain(item) for item in state.fault_rules),
            fault_tests=tuple(
                FaultTestResponse.from_domain(item, state.sequence) for item in state.fault_tests
            ),
        )


class StartFaultRequest(StrictRequest):
    device_id: str = Field(min_length=1)
    fault_type: str = Field(min_length=1)
    point_id: str | None = None
    test_value: float | None = None
    delay_steps: int = Field(default=0, ge=0)
    duration_steps: int | None = Field(default=None, ge=1)

    def to_domain(self) -> FaultRequest:
        try:
            fault_type = FaultType(self.fault_type)
        except ValueError as exc:
            raise ValueError("unknown problem to test") from exc
        return FaultRequest(
            device_id=self.device_id,
            fault_type=fault_type,
            point_id=self.point_id,
            test_value=self.test_value,
            delay_steps=self.delay_steps,
            duration_steps=self.duration_steps,
        )


def _service(request: Request) -> SimulationService:
    if not get_settings().simulation_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="simulation is unavailable",
        )
    service = getattr(request.app.state, "simulation", None)
    if not isinstance(service, SimulationService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=getattr(request.app.state, "simulation_error", "simulation is not ready"),
        )
    return service


def _gateway(request: Request) -> GatewayStatus:
    gateway = getattr(request.app.state, "modbus_gateway", None)
    if isinstance(gateway, ModbusGateway):
        return gateway.status()
    return stopped_gateway_status(get_settings())


def _response(request: Request, state: SimulationState) -> SimulationResponse:
    result = SimulationResponse.from_domain(state, _gateway(request))
    store = getattr(request.app.state, "configuration", None)
    if store is not None:
        result.configuration_generation = store.head("simulation")["generation"]
    return result


def _input_response(inputs: SimulationInputs) -> InputRequest:
    values = asdict(inputs)
    if isinstance(inputs, BatteryInputs):
        return BatteryInputRequest(kind="battery", **values)
    if isinstance(inputs, ConverterInputs):
        return ConverterInputRequest(kind="converter", **values)
    if isinstance(inputs, StabilitiInputs):
        return StabilitiInputRequest(kind="stabiliti", **values)
    values.pop("demand_updated_at", None)
    values.pop("available_updated_at", None)
    return Dc600InputRequest(kind="dc600", **values)


def _fault_http_error(exc: ValueError) -> HTTPException:
    message = str(exc)
    code = (
        status.HTTP_404_NOT_FOUND
        if message.startswith(("unknown simulation device", "unknown fault test"))
        else status.HTTP_400_BAD_REQUEST
    )
    return HTTPException(status_code=code, detail=message)


@router.get("/api/v1/simulation", response_model=SimulationResponse)
def simulation_state(request: Request) -> SimulationResponse:
    """Return current runtime and inventory state."""

    return _response(request, _service(request).state())


@router.post("/api/v1/simulation/start", response_model=SimulationResponse)
def start(request: Request) -> SimulationResponse:
    """Start deterministic runtime stepping."""

    return _response(request, _service(request).start())


@router.post("/api/v1/simulation/pause", response_model=SimulationResponse)
def pause(request: Request) -> SimulationResponse:
    """Pause deterministic runtime stepping."""

    return _response(request, _service(request).pause())


@router.post("/api/v1/simulation/step", response_model=SimulationResponse)
def step(request: Request) -> SimulationResponse:
    """Advance exactly one fixed simulation frame."""

    return _response(request, _service(request).step())


@router.post("/api/v1/simulation/reset", response_model=SimulationResponse)
def reset(request: Request) -> SimulationResponse:
    """Return the runtime to its initial state."""

    return _response(request, _service(request).reset())


@router.post("/api/v1/simulation/replay", response_model=SimulationResponse)
def replay(request: Request) -> SimulationResponse:
    """Repeat the recorded step count and verify its trace."""

    return _response(request, _service(request).replay())


@router.get("/api/v1/simulation/faults", response_model=FaultCatalogResponse)
def get_faults(request: Request) -> FaultCatalogResponse:
    """Return exact supported fault rules and the current test session."""

    service = _service(request)
    state = service.state()
    return FaultCatalogResponse(
        rules=tuple(FaultRuleResponse.from_domain(item) for item in service.fault_rules()),
        tests=tuple(
            FaultTestResponse.from_domain(item, state.sequence) for item in state.fault_tests
        ),
    )


@router.post("/api/v1/simulation/faults", response_model=SimulationResponse)
def start_fault(payload: StartFaultRequest, request: Request) -> SimulationResponse:
    """Schedule one supported, simulation-only fault test."""

    service = _service(request)
    try:
        state = service.start_fault(payload.to_domain())
    except ValueError as exc:
        raise _fault_http_error(exc) from None
    return _response(request, state)


@router.post(
    "/api/v1/simulation/faults/{test_id}/clear",
    response_model=SimulationResponse,
)
def clear_fault(test_id: str, request: Request) -> SimulationResponse:
    """Clear or cancel one tracked fault test."""

    service = _service(request)
    try:
        state = service.clear_fault(test_id)
    except ValueError as exc:
        raise _fault_http_error(exc) from None
    return _response(request, state)


@router.get("/api/v1/simulation/report")
def download_report(request: Request) -> Response:
    """Download the deterministic fault-test session report."""

    return Response(
        _service(request).report_bytes(),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="simulator-report.json"'},
    )


@router.post(
    "/api/v1/simulation/devices/{device_id}/inputs",
    response_model=SimulationResponse,
)
def set_device_inputs(
    device_id: str,
    payload: InputRequest,
    request: Request,
) -> SimulationResponse:
    """Save checked test inputs for the device's next simulation step."""

    raise HTTPException(
        409, "Save device inputs for review, then apply them from Settings history."
    )


@router.get("/api/v1/simulation/snapshot", response_model=SimulationResponse)
def snapshot(request: Request) -> SimulationResponse:
    """Return the typed snapshot and trace summary."""

    return _response(request, _service(request).state())


class BatterySettingsRequest(StrictRequest):
    capacity_kwh: float = Field(strict=True, gt=0, allow_inf_nan=False)
    initial_level_percent: float = Field(strict=True, ge=0, le=100, allow_inf_nan=False)
    charge_efficiency_percent: float = Field(strict=True, gt=0, le=100, allow_inf_nan=False)
    discharge_efficiency_percent: float = Field(strict=True, gt=0, le=100, allow_inf_nan=False)
    charge_limit_kw: float = Field(strict=True, ge=0, allow_inf_nan=False)
    discharge_limit_kw: float = Field(strict=True, ge=0, allow_inf_nan=False)

    def to_domain(self) -> BatteryConfig:
        return BatteryConfig(
            "custom",
            self.capacity_kwh,
            self.capacity_kwh * (self.initial_level_percent / 100),
            self.charge_efficiency_percent / 100,
            self.discharge_efficiency_percent / 100,
            self.charge_limit_kw,
            self.discharge_limit_kw,
        )


class BusSettingsRequest(StrictRequest):
    nominal_voltage_v: float = Field(strict=True, gt=0, allow_inf_nan=False)
    max_voltage_v: float = Field(strict=True, gt=0, allow_inf_nan=False)
    capacitance_f: float = Field(strict=True, gt=0, allow_inf_nan=False)
    loss_percent: float = Field(strict=True, ge=0, lt=100, allow_inf_nan=False)
    tolerance_kw: float = Field(strict=True, gt=0, allow_inf_nan=False)

    def to_domain(self) -> BusConfig:
        return BusConfig(
            self.nominal_voltage_v,
            self.max_voltage_v,
            self.capacitance_f,
            self.loss_percent / 100,
            self.tolerance_kw,
        )


class ProfileRequest(StrictRequest):
    battery_profile: str | None = None
    battery_settings: BatterySettingsRequest | None = None
    bus_settings: BusSettingsRequest | None = None
    profile_id: str
    seed: int = Field(strict=True, ge=0, le=2147483647)


class ProfilesResponse(BaseModel):
    profiles: tuple[str, ...]


@router.get("/api/v1/simulation/profiles", response_model=ProfilesResponse)
def profiles(request: Request) -> ProfilesResponse:
    _service(request)
    return ProfilesResponse(profiles=PROFILE_IDS)


@router.post("/api/v1/simulation/profile", response_model=SimulationResponse)
def select_profile(payload: ProfileRequest, request: Request) -> SimulationResponse:
    raise HTTPException(409, "Save the setup for review, then apply it from Settings history.")


@router.post("/api/v1/simulation/snapshot", response_model=SimulationResponse)
def save_snapshot(request: Request) -> SimulationResponse:
    """Permanently save one complete checkpoint."""

    return _response(request, _service(request).save_snapshot())


@router.post("/api/v1/simulation/restore", response_model=SimulationResponse)
def restore(request: Request) -> SimulationResponse:
    service = _service(request)
    try:
        state = service.restore()
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    return _response(request, state)


@router.post("/api/v1/simulation/new", response_model=SimulationResponse)
def new_run(request: Request) -> SimulationResponse:
    try:
        state = _service(request).new_run()
    except ValueError:
        raise HTTPException(
            409, "Pause first and check that saved settings are compatible."
        ) from None
    return _response(request, state)


@router.delete("/api/v1/simulation/snapshot", response_model=SimulationResponse)
def delete_saved_run(request: Request) -> SimulationResponse:
    return _response(request, _service(request).delete_saved_run())
