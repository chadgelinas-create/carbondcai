import type { ApiClient } from "./client";
import { isOneOf, isRecord } from "./currentState";

export type SimulationAction =
  "start" | "pause" | "step" | "reset" | "replay" | "snapshot" | "restore" | "new" | "delete";
export type SimulationLifecycle = "stopped" | "running" | "shutdown";
export type SimulationGatewayState = "ready" | "stopped" | "problem";

export interface SimulationCapability {
  capability_id: string;
  kind: string;
  evidence: string;
  fidelity: string;
  review_status: string;
  available: boolean;
  enabled_profiles: string[];
  source_ids: string[];
  decision_ids: string[];
  assumptions: string[];
}

export interface SimulationValue {
  logical_id: string;
  value: boolean | number | string;
  unit: string;
  quality: string;
  source: string;
  provisional: boolean;
  test_id: string | null;
  test_started_at: string | null;
}

export interface SimulationFault {
  fault_id: string;
  active: boolean;
  severity: string;
  source: string;
  test_id: string | null;
  test_started_at: string | null;
}

export type FaultTestState = "scheduled" | "active" | "cleared" | "complete";

export interface SimulationFaultRule {
  fault_type: string;
  label: string;
  device_ids: string[];
  needs_point: boolean;
  needs_value: boolean;
  latched: boolean;
  expected_result: string;
  clear_method: string;
  fidelity: string;
  evidence: string;
}

export interface SimulationFaultTest {
  test_id: string;
  device_id: string;
  fault_type: string;
  label: string;
  point_id: string | null;
  test_value: number | null;
  start_step: number;
  duration_steps: number | null;
  started_at: string;
  cleared_at: string | null;
  state: FaultTestState;
  latched: boolean;
  expected_result: string;
  clear_method: string;
}

export interface StartSimulationFault {
  device_id: string;
  fault_type: string;
  point_id: string | null;
  test_value: number | null;
  delay_steps: number;
  duration_steps: number | null;
}

export interface BatteryInputs {
  kind: "battery";
  cluster_soc: number;
  voltage: number;
  current: number;
  maximum_temperature: number;
  charge_power_limit: number;
  discharge_power_limit: number;
  main_contactor: boolean;
  precharge_contactor: boolean;
  precharge_state: number;
  fire_communication_state: number;
  communication_ok: boolean;
  fault_active: boolean;
}

export interface ConverterInputs {
  kind: "converter";
  enabled: boolean;
  requested_power: number;
  available_power: number;
  charge_power_limit: number;
  discharge_power_limit: number;
  dc_voltage: number;
  dc_current: number;
  temperature: number;
  fault_active: boolean;
  communication_ok: boolean;
  pv_available: boolean;
  mppt_applying: boolean;
  test_ramp_per_step: number | null;
  test_derate_limit: number | null;
}

export interface StabilitiInputs {
  kind: "stabiliti";
  enabled: boolean;
  available_power: number;
  actual_power: number;
  bus_load: number | null;
  power_limit: number;
  maximum_temperature: number;
  derated: boolean;
  communication_ok: boolean;
  fault_active: boolean;
}

export interface Dc600Inputs {
  kind: "dc600";
  demand_power: number | null;
  available_power: number | null;
  actual_power: number | null;
  demand_fresh: boolean;
  available_fresh: boolean;
  session_active: boolean;
  communication_ok: boolean;
  fault_active: boolean;
}

export type SimulationInputs = BatteryInputs | ConverterInputs | StabilitiInputs | Dc600Inputs;

export interface SimulationDevice {
  device_id: string;
  display_name: string;
  role: string;
  lifecycle: string;
  health: string;
  test_id: string | null;
  test_started_at: string | null;
  implementation_status: string;
  protocol_status: string;
  protocol: string;
  endpoint_status: string;
  model_status: string;
  firmware_status: string;
  blocker_reason: string | null;
  capabilities: SimulationCapability[];
  values: SimulationValue[];
  faults: SimulationFault[];
  inputs: SimulationInputs;
  applied_inputs: SimulationInputs;
  inputs_pending: boolean;
  status_text: string;
  testing_method: string;
  direct_connection: string;
  test_notice: string | null;
}

export interface SimulationRegister {
  logical_id: string;
  function: number;
  address: number;
  display: string;
  count: number;
  unit: string;
}

export interface SimulationGatewayRoute {
  device_id: string;
  modbus_id: number;
  functions: number[];
  registers: SimulationRegister[];
}

export interface SimulationGateway {
  state: SimulationGatewayState;
  host: string;
  port: number;
  clients: number;
  last_request: string | null;
  routes: SimulationGatewayRoute[];
}

export interface SimulationActivity {
  sequence: number;
  at: string;
  device_id: string;
  category: string;
  message: string;
  test_id: string | null;
  test_started_at: string | null;
}

export interface StabilitiFleet {
  ready: number;
  available: number;
  derated: number;
  faulted: number;
  total: number;
  available_power: number;
}

export type ValueStatus = "available" | "missing" | "blocked" | "not_implemented";
export type PlantQuality = "good" | "stale" | "invalid" | "comm_error" | "out_of_range";

export interface PlantValue {
  name: string;
  value: number | null;
  unit: string;
  status: ValueStatus;
  quality: PlantQuality | null;
  at: string | null;
  reason: string;
  capability_id: string;
}

export interface PlantCapability {
  capability_id: string;
  device_id: string;
  kind: string;
  evidence_classification: "sourced" | "calibrated" | "provisional" | "blocked" | "unsupported";
  fidelity_level: "contract" | "level_1" | "level_2" | "hardware_calibrated";
  review_status: string;
  available: boolean;
  enabled_profiles: string[];
  source_ids: string[];
  decision_ids: string[];
  assumptions: string[];
  calibration_evidence: string | null;
  calibration_tolerance: string | null;
}

export const profileNames: Readonly<Record<string, string>> = {
  stage0: "Basic simulation",
  solar_step: "Sudden solar change",
  solar_ramp: "Gradual solar change",
  solar_daylight: "Daylight",
  solar_cloud: "Passing cloud",
  solar_variable: "Changing sunlight",
};

export const batteryNames: Readonly<Record<string, string>> = {
  custom: "Custom settings",
  battery_2700: "2,700 kWh",
  battery_3500: "3,500 kWh",
  battery_3850: "3,850 kWh",
};

export interface BatterySettings {
  capacity_kwh: number;
  initial_level_percent: number;
  charge_efficiency_percent: number;
  discharge_efficiency_percent: number;
  charge_limit_kw: number;
  discharge_limit_kw: number;
}

export interface BusSettings {
  nominal_voltage_v: number;
  max_voltage_v: number;
  capacitance_f: number;
  loss_percent: number;
  tolerance_kw: number;
}

export interface BatteryConfig {
  profile_id: string;
  capacity_kwh: number;
  initial_energy_kwh: number;
  charge_efficiency: number;
  discharge_efficiency: number;
  charge_limit_kw: number;
  discharge_limit_kw: number;
  revision: string;
}

export const batteryFields = [
  ["Battery energy", "kWh"],
  ["Battery SOC", "ratio"],
  ["Requested power", "kW"],
  ["Accepted power", "kW"],
  ["Charge limit", "kW"],
  ["Discharge limit", "kW"],
  ["Offered power", "kW"],
  ["Actual power", "kW"],
  ["Energy loss rate", "kW"],
  ["Energy change", "kWh"],
  ["Request not met", "kW"],
  ["Balance error", "kW"],
] as const;
const batteryLedgerFields = [
  ["Battery energy change", "kWh"],
  ["Battery loss", "kW"],
  ["Battery balance error", "kW"],
] as const;

export interface PvConfig {
  device_id: string;
  destination: "dc";
  efficiency: number;
  limit_kw: number;
  points: [number, number][];
  interpolation: "hold" | "linear";
  interval_seconds: number;
  revision: string;
  unit: "kW";
  end: "hold";
}

export interface PvPath {
  device_id: string;
  destination: "dc";
  health: string;
  reason: string;
  values: PlantValue[];
}

export interface PvSample extends BusSample {
  paths: PvPath[];
}

export interface PlantConfig {
  battery: BatteryConfig | null;
  pv: PvConfig[];
  profile_id: string;
  revision: string;
  model_version: string;
  seed: number;
  step_seconds: number;
  initial_at: string;
  capabilities: PlantCapability[];
  limitations: string[];
  review_owner: string;
  reviewers: string[];
  bus: {
    nominal_voltage_v: number;
    max_voltage_v: number;
    capacitance_f: number;
    loss_ratio: number;
    tolerance_kw: number;
  };
}

export interface PlantPort {
  device_id: string;
  at: string;
  sequence: number;
  health: string;
  readings: PlantValue[];
  outputs: PlantValue[];
  ledger: PlantValue[];
}

export interface PlantState {
  pv_paths: PvPath[];
  configuration: PlantConfig;
  at: string;
  sequence: number;
  bus: PlantValue[];
  pv: PlantValue[];
  bess: PlantValue[];
  load: PlantValue[];
  converter: PlantValue[];
  thermal: PlantValue[];
  ledger: PlantValue[];
  balance: ValueStatus;
  ports: PlantPort[];
}

export interface FrameFailure {
  sequence: number;
  at: string;
  code: "invalid_plant_step" | "plant_step_failed" | "replay_mismatch";
}

export interface SavedRunInfo {
  saved_at: string;
  simulation_at: string;
  sequence: number;
}

export interface SimulationState {
  configuration_generation?: number;
  battery_history: BusSample[];
  pv_history: PvSample[];
  bus_history: BusSample[];
  plant: PlantState;
  plant_trace_hash: string;
  failure: FrameFailure | null;
  snapshot_saved: boolean;
  saved_run: SavedRunInfo | null;
  restored_run: SavedRunInfo | null;
  lifecycle: SimulationLifecycle;
  at: string;
  sequence: number;
  trace_entries: number;
  trace_hash: string;
  devices: SimulationDevice[];
  activity: SimulationActivity[];
  stabiliti_fleet: StabilitiFleet;
  gateway: SimulationGateway;
  fault_rules: SimulationFaultRule[];
  fault_tests: SimulationFaultTest[];
}

export interface BusSample {
  at: string;
  sequence: number;
  values: PlantValue[];
}

const actionPaths: Record<SimulationAction, string> = {
  new: "/v1/simulation/new",
  delete: "/v1/simulation/snapshot",
  start: "/v1/simulation/start",
  pause: "/v1/simulation/pause",
  step: "/v1/simulation/step",
  reset: "/v1/simulation/reset",
  replay: "/v1/simulation/replay",
  snapshot: "/v1/simulation/snapshot",
  restore: "/v1/simulation/restore",
};

function isStrings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value);
}

function isOptionalNumber(value: unknown): value is number | null {
  return value === null || isNumber(value);
}

function isOptionalString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isCapability(value: unknown): value is SimulationCapability {
  return (
    isRecord(value) &&
    typeof value.capability_id === "string" &&
    typeof value.kind === "string" &&
    typeof value.evidence === "string" &&
    typeof value.fidelity === "string" &&
    typeof value.review_status === "string" &&
    typeof value.available === "boolean" &&
    isStrings(value.enabled_profiles) &&
    isStrings(value.source_ids) &&
    isStrings(value.decision_ids) &&
    isStrings(value.assumptions)
  );
}

function isValue(value: unknown): value is SimulationValue {
  return (
    isRecord(value) &&
    typeof value.logical_id === "string" &&
    (typeof value.value === "boolean" ||
      typeof value.value === "string" ||
      isNumber(value.value)) &&
    typeof value.unit === "string" &&
    typeof value.quality === "string" &&
    typeof value.source === "string" &&
    typeof value.provisional === "boolean" &&
    isOptionalString(value.test_id) &&
    isOptionalString(value.test_started_at)
  );
}

function isFault(value: unknown): value is SimulationFault {
  return (
    isRecord(value) &&
    typeof value.fault_id === "string" &&
    typeof value.active === "boolean" &&
    typeof value.severity === "string" &&
    typeof value.source === "string" &&
    isOptionalString(value.test_id) &&
    isOptionalString(value.test_started_at)
  );
}

function isFaultRule(value: unknown): value is SimulationFaultRule {
  return (
    isRecord(value) &&
    typeof value.fault_type === "string" &&
    typeof value.label === "string" &&
    isStrings(value.device_ids) &&
    typeof value.needs_point === "boolean" &&
    typeof value.needs_value === "boolean" &&
    typeof value.latched === "boolean" &&
    typeof value.expected_result === "string" &&
    typeof value.clear_method === "string" &&
    typeof value.fidelity === "string" &&
    typeof value.evidence === "string"
  );
}

function isFaultTest(value: unknown): value is SimulationFaultTest {
  return (
    isRecord(value) &&
    typeof value.test_id === "string" &&
    typeof value.device_id === "string" &&
    typeof value.fault_type === "string" &&
    typeof value.label === "string" &&
    isOptionalString(value.point_id) &&
    isOptionalNumber(value.test_value) &&
    isInteger(value.start_step) &&
    (value.duration_steps === null || isInteger(value.duration_steps)) &&
    typeof value.started_at === "string" &&
    isOptionalString(value.cleared_at) &&
    isOneOf(value.state, ["scheduled", "active", "cleared", "complete"]) &&
    typeof value.latched === "boolean" &&
    typeof value.expected_result === "string" &&
    typeof value.clear_method === "string"
  );
}

function isBatteryInputs(value: unknown): value is BatteryInputs {
  return (
    isRecord(value) &&
    value.kind === "battery" &&
    isNumber(value.cluster_soc) &&
    isNumber(value.voltage) &&
    isNumber(value.current) &&
    isNumber(value.maximum_temperature) &&
    isNumber(value.charge_power_limit) &&
    isNumber(value.discharge_power_limit) &&
    typeof value.main_contactor === "boolean" &&
    typeof value.precharge_contactor === "boolean" &&
    isInteger(value.precharge_state) &&
    isInteger(value.fire_communication_state) &&
    typeof value.communication_ok === "boolean" &&
    typeof value.fault_active === "boolean"
  );
}

function isConverterInputs(value: unknown): value is ConverterInputs {
  return (
    isRecord(value) &&
    value.kind === "converter" &&
    typeof value.enabled === "boolean" &&
    isNumber(value.requested_power) &&
    isNumber(value.available_power) &&
    isNumber(value.charge_power_limit) &&
    isNumber(value.discharge_power_limit) &&
    isNumber(value.dc_voltage) &&
    isNumber(value.dc_current) &&
    isNumber(value.temperature) &&
    typeof value.fault_active === "boolean" &&
    typeof value.communication_ok === "boolean" &&
    typeof value.pv_available === "boolean" &&
    typeof value.mppt_applying === "boolean" &&
    isOptionalNumber(value.test_ramp_per_step) &&
    isOptionalNumber(value.test_derate_limit)
  );
}

function isStabilitiInputs(value: unknown): value is StabilitiInputs {
  return (
    isRecord(value) &&
    value.kind === "stabiliti" &&
    typeof value.enabled === "boolean" &&
    isNumber(value.available_power) &&
    isNumber(value.actual_power) &&
    isOptionalNumber(value.bus_load) &&
    isNumber(value.power_limit) &&
    isNumber(value.maximum_temperature) &&
    typeof value.derated === "boolean" &&
    typeof value.communication_ok === "boolean" &&
    typeof value.fault_active === "boolean"
  );
}

function isDc600Inputs(value: unknown): value is Dc600Inputs {
  return (
    isRecord(value) &&
    value.kind === "dc600" &&
    isOptionalNumber(value.demand_power) &&
    isOptionalNumber(value.available_power) &&
    isOptionalNumber(value.actual_power) &&
    typeof value.demand_fresh === "boolean" &&
    typeof value.available_fresh === "boolean" &&
    typeof value.session_active === "boolean" &&
    typeof value.communication_ok === "boolean" &&
    typeof value.fault_active === "boolean"
  );
}

function isInputs(value: unknown): value is SimulationInputs {
  return (
    isBatteryInputs(value) ||
    isConverterInputs(value) ||
    isStabilitiInputs(value) ||
    isDc600Inputs(value)
  );
}

function isDevice(value: unknown): value is SimulationDevice {
  return (
    isRecord(value) &&
    typeof value.device_id === "string" &&
    typeof value.display_name === "string" &&
    typeof value.role === "string" &&
    typeof value.lifecycle === "string" &&
    typeof value.health === "string" &&
    isOptionalString(value.test_id) &&
    isOptionalString(value.test_started_at) &&
    typeof value.implementation_status === "string" &&
    typeof value.protocol_status === "string" &&
    typeof value.protocol === "string" &&
    typeof value.endpoint_status === "string" &&
    typeof value.model_status === "string" &&
    typeof value.firmware_status === "string" &&
    (value.blocker_reason === null || typeof value.blocker_reason === "string") &&
    Array.isArray(value.capabilities) &&
    value.capabilities.every(isCapability) &&
    Array.isArray(value.values) &&
    value.values.every(isValue) &&
    Array.isArray(value.faults) &&
    value.faults.every(isFault) &&
    isInputs(value.inputs) &&
    isInputs(value.applied_inputs) &&
    value.inputs.kind === value.applied_inputs.kind &&
    typeof value.inputs_pending === "boolean" &&
    typeof value.status_text === "string" &&
    typeof value.testing_method === "string" &&
    typeof value.direct_connection === "string" &&
    (value.test_notice === null || typeof value.test_notice === "string")
  );
}

function isRegister(value: unknown): value is SimulationRegister {
  return (
    isRecord(value) &&
    typeof value.logical_id === "string" &&
    isInteger(value.function) &&
    isInteger(value.address) &&
    typeof value.display === "string" &&
    isInteger(value.count) &&
    typeof value.unit === "string"
  );
}

function isGatewayRoute(value: unknown): value is SimulationGatewayRoute {
  return (
    isRecord(value) &&
    typeof value.device_id === "string" &&
    isInteger(value.modbus_id) &&
    value.modbus_id >= 1 &&
    value.modbus_id <= 247 &&
    Array.isArray(value.functions) &&
    value.functions.every(isInteger) &&
    Array.isArray(value.registers) &&
    value.registers.every(isRegister)
  );
}

function isGateway(value: unknown): value is SimulationGateway {
  return (
    isRecord(value) &&
    isOneOf(value.state, ["ready", "stopped", "problem"]) &&
    typeof value.host === "string" &&
    isInteger(value.port) &&
    value.port >= 1 &&
    value.port <= 65535 &&
    isInteger(value.clients) &&
    value.clients >= 0 &&
    (value.last_request === null || typeof value.last_request === "string") &&
    Array.isArray(value.routes) &&
    value.routes.every(isGatewayRoute)
  );
}

function isActivity(value: unknown): value is SimulationActivity {
  return (
    isRecord(value) &&
    isInteger(value.sequence) &&
    typeof value.at === "string" &&
    typeof value.device_id === "string" &&
    typeof value.category === "string" &&
    typeof value.message === "string" &&
    isOptionalString(value.test_id) &&
    isOptionalString(value.test_started_at)
  );
}

function isFleet(value: unknown): value is StabilitiFleet {
  return (
    isRecord(value) &&
    isInteger(value.ready) &&
    isInteger(value.available) &&
    isInteger(value.derated) &&
    isInteger(value.faulted) &&
    isInteger(value.total) &&
    isNumber(value.available_power)
  );
}

function isDate(value: unknown): value is string {
  return typeof value === "string" && Number.isFinite(Date.parse(value));
}

function isPlantValue(value: unknown): value is PlantValue {
  if (
    !isRecord(value) ||
    typeof value.name !== "string" ||
    !isOptionalNumber(value.value) ||
    typeof value.unit !== "string" ||
    typeof value.reason !== "string" ||
    typeof value.capability_id !== "string" ||
    !isOneOf(value.status, ["available", "missing", "blocked", "not_implemented"]) ||
    (value.quality !== null &&
      !isOneOf(value.quality, ["good", "stale", "invalid", "comm_error", "out_of_range"])) ||
    (value.at !== null && !isDate(value.at))
  )
    return false;
  return value.status === "available"
    ? value.value !== null && value.quality !== null && value.at !== null
    : value.value === null;
}

function isPlantCapability(value: unknown): value is PlantCapability {
  return (
    isRecord(value) &&
    typeof value.capability_id === "string" &&
    value.device_id === "plant" &&
    isOneOf(value.kind, ["mode", "command", "fault", "transport", "behavior"]) &&
    isOneOf(value.evidence_classification, [
      "sourced",
      "calibrated",
      "provisional",
      "blocked",
      "unsupported",
    ]) &&
    isOneOf(value.fidelity_level, ["contract", "level_1", "level_2", "hardware_calibrated"]) &&
    typeof value.review_status === "string" &&
    typeof value.available === "boolean" &&
    isStrings(value.enabled_profiles) &&
    isStrings(value.source_ids) &&
    isStrings(value.decision_ids) &&
    isStrings(value.assumptions) &&
    isOptionalString(value.calibration_evidence) &&
    isOptionalString(value.calibration_tolerance) &&
    (!(
      value.evidence_classification === "blocked" || value.evidence_classification === "unsupported"
    ) ||
      !value.available) &&
    (value.available || value.enabled_profiles.length === 0)
  );
}

const pvIds = ["cnvtr-2"];
export const pvFields = [
  "Available solar",
  "Requested power",
  "Accepted power",
  "Output limit",
  "Offered power",
  "Power delivered",
  "Conversion loss",
  "Unused solar",
];

function isPvConfig(value: unknown): value is PvConfig {
  if (
    !isRecord(value) ||
    !isOneOf(value.device_id, pvIds) ||
    value.destination !== "dc" ||
    !isNumber(value.efficiency) ||
    value.efficiency <= 0 ||
    value.efficiency > 1 ||
    !isNumber(value.limit_kw) ||
    value.limit_kw < 0 ||
    !isNumber(value.interval_seconds) ||
    value.interval_seconds <= 0 ||
    value.revision !== "solar-2" ||
    value.unit !== "kW" ||
    value.end !== "hold" ||
    !isOneOf(value.interpolation, ["hold", "linear"]) ||
    !Array.isArray(value.points) ||
    value.points.length === 0
  )
    return false;
  let previous = -1;
  return value.points.every((point: unknown, index: number) => {
    if (
      !Array.isArray(point) ||
      point.length !== 2 ||
      !isNumber(point[0]) ||
      !isNumber(point[1]) ||
      point[1] < 0 ||
      point[1] > 6553.5 ||
      point[0] <= previous ||
      (index === 0 && point[0] !== 0)
    )
      return false;
    previous = point[0];
    return true;
  });
}

function isPvPaths(value: unknown, config: PlantConfig, at: string): value is PvPath[] {
  return (
    Array.isArray(value) &&
    value.length === config.pv.length &&
    value.every(
      (path: unknown, index: number) =>
        isRecord(path) &&
        path.device_id === config.pv[index]?.device_id &&
        path.destination === config.pv[index]?.destination &&
        isOneOf(path.health, ["healthy", "degraded", "unavailable", "stopped", "disabled"]) &&
        typeof path.reason === "string" &&
        path.reason.length > 0 &&
        hasFields(
          path.values,
          pvFields.map((name) => [name, "kW"] as const),
        ) &&
        path.values.every(
          (item) =>
            item.capability_id === "plant.pv" &&
            (item.value === null || (item.value >= 0 && item.quality === "good" && item.at === at)),
        ),
    )
  );
}

function isPvHistory(value: unknown, plant: PlantState): value is PvSample[] {
  if (!Array.isArray(value) || value.length !== Math.min(plant.sequence, 120)) return false;
  return value.every(
    (sample: unknown, index: number) =>
      isRecord(sample) &&
      isDate(sample.at) &&
      sample.sequence === plant.sequence - value.length + index + 1 &&
      Date.parse(sample.at) <= Date.parse(plant.at) &&
      (index === 0 || Date.parse(sample.at) > Date.parse((value[index - 1] as PvSample).at)) &&
      (index !== value.length - 1 || sample.at === plant.at) &&
      isPvPaths(sample.paths, plant.configuration, sample.at) &&
      hasFields(
        sample.values,
        plant.pv.map((item) => [item.name, item.unit] as const),
      ) &&
      sample.values.every(
        (item) => item.at === null || Date.parse(item.at) <= Date.parse(sample.at as string),
      ),
  );
}

function isBatteryConfig(value: unknown): value is BatteryConfig | null {
  if (value === null) return true;
  return (
    isRecord(value) &&
    typeof value.profile_id === "string" &&
    Object.hasOwn(batteryNames, value.profile_id) &&
    value.revision === "battery-1" &&
    isNumber(value.capacity_kwh) &&
    value.capacity_kwh > 0 &&
    isNumber(value.initial_energy_kwh) &&
    value.initial_energy_kwh >= 0 &&
    value.initial_energy_kwh <= value.capacity_kwh &&
    isNumber(value.charge_efficiency) &&
    value.charge_efficiency > 0 &&
    value.charge_efficiency <= 1 &&
    isNumber(value.discharge_efficiency) &&
    value.discharge_efficiency > 0 &&
    value.discharge_efficiency <= 1 &&
    isNumber(value.charge_limit_kw) &&
    value.charge_limit_kw >= 0 &&
    isNumber(value.discharge_limit_kw) &&
    value.discharge_limit_kw >= 0
  );
}

function isBattery(values: unknown, config: BatteryConfig | null): boolean {
  if (config === null)
    return (
      hasFields(values, batteryFields.slice(0, 2)) && values.every((value) => value.value === null)
    );
  if (!hasFields(values, batteryFields)) return false;
  const energy = values[0]?.value;
  const level = values[1]?.value;
  return (
    isNumber(energy) &&
    isNumber(level) &&
    energy >= 0 &&
    energy <= config.capacity_kwh &&
    level === energy / config.capacity_kwh &&
    values.every((value) => value.capability_id === "plant.bess")
  );
}

function isPlantConfig(value: unknown): value is PlantConfig {
  return (
    isRecord(value) &&
    typeof value.profile_id === "string" &&
    Object.hasOwn(profileNames, value.profile_id) &&
    Array.isArray(value.pv) &&
    value.pv.every(isPvConfig) &&
    value.pv.length === (value.profile_id === "stage0" ? 0 : pvIds.length) &&
    value.pv.every((path: PvConfig, index: number) => path.device_id === pvIds[index]) &&
    typeof value.revision === "string" &&
    value.model_version === "5" &&
    isBatteryConfig(value.battery) &&
    isRecord(value.bus) &&
    isNumber(value.bus.nominal_voltage_v) &&
    isNumber(value.bus.max_voltage_v) &&
    value.bus.nominal_voltage_v > 0 &&
    value.bus.max_voltage_v >= value.bus.nominal_voltage_v &&
    isNumber(value.bus.capacitance_f) &&
    value.bus.capacitance_f > 0 &&
    isNumber(value.bus.loss_ratio) &&
    value.bus.loss_ratio >= 0 &&
    value.bus.loss_ratio < 1 &&
    isNumber(value.bus.tolerance_kw) &&
    value.bus.tolerance_kw > 0 &&
    isInteger(value.seed) &&
    value.seed >= 0 &&
    value.seed <= 2147483647 &&
    isNumber(value.step_seconds) &&
    value.step_seconds >= 0.001 &&
    value.step_seconds <= 60 &&
    isDate(value.initial_at) &&
    Array.isArray(value.capabilities) &&
    value.capabilities.every(isPlantCapability) &&
    isStrings(value.limitations) &&
    typeof value.review_owner === "string" &&
    isStrings(value.reviewers)
  );
}

function hasFields(
  value: unknown,
  fields: readonly (readonly [string, string])[],
): value is PlantValue[] {
  return (
    Array.isArray(value) &&
    value.length === fields.length &&
    value.every(
      (reading: unknown, index: number) =>
        isPlantValue(reading) &&
        reading.name === fields[index]?.[0] &&
        reading.unit === fields[index][1],
    )
  );
}

const busFields = [
  ["Bus voltage", "VDC"],
  ["Bus energy", "kWh"],
  ["Power difference", "kW"],
  ["Steps without calculation", "count"],
] as const;
const portFields = [
  ["Requested power", "kW"],
  ["Accepted power", "kW"],
  ["Power supplied", "kW"],
  ["Power used", "kW"],
  ["Modeled loss", "kW"],
  ["Unused supply", "kW"],
  ["Power not supplied", "kW"],
  ["Energy transfer", "kWh"],
] as const;
const ledgerFields = [
  ["Offered supply", "kW"],
  ["Requested load", "kW"],
  ["Accepted load", "kW"],
  ["Power supplied", "kW"],
  ["Power used", "kW"],
  ["Modeled loss", "kW"],
  ["Stored power", "kW"],
  ["Energy change", "kWh"],
  ["Unused supply", "kW"],
  ["Power not supplied", "kW"],
  ["Balance error", "kW"],
  ["Excluded inputs", "count"],
] as const;

function isPlantPort(value: unknown): value is PlantPort {
  return (
    isRecord(value) &&
    typeof value.device_id === "string" &&
    isDate(value.at) &&
    isInteger(value.sequence) &&
    value.sequence >= 0 &&
    isOneOf(value.health, ["healthy", "degraded", "unavailable", "stopped", "disabled"]) &&
    Array.isArray(value.readings) &&
    value.readings.every(isPlantValue) &&
    Array.isArray(value.outputs) &&
    value.outputs.every(isPlantValue) &&
    hasFields(value.ledger, portFields)
  );
}

function isPlant(value: unknown): value is PlantState {
  if (
    !isRecord(value) ||
    !isPlantConfig(value.configuration) ||
    !isDate(value.at) ||
    !isInteger(value.sequence) ||
    value.sequence < 0 ||
    !isOneOf(value.balance, ["available", "missing", "blocked", "not_implemented"]) ||
    !Array.isArray(value.ports) ||
    !value.ports.every(isPlantPort)
  )
    return false;
  const ids = ["ba1-1", "cnvtr-1", "cnvtr-2", "dc600", "st-4", "st-5", "st-6"];
  return (
    isPvPaths(value.pv_paths, value.configuration, value.at) &&
    hasFields(value.bus, busFields) &&
    hasFields(
      value.ledger,
      value.configuration.battery === null
        ? ledgerFields
        : [...ledgerFields, ...batteryLedgerFields],
    ) &&
    isBattery(value.bess, value.configuration.battery) &&
    [
      value.bus,
      value.pv,
      value.bess,
      value.load,
      value.converter,
      value.thermal,
      value.ledger,
    ].every((group) => Array.isArray(group) && group.every(isPlantValue)) &&
    value.ports.length === ids.length &&
    value.ports.every(
      (port, index) =>
        port.device_id === ids[index] && port.at === value.at && port.sequence === value.sequence,
    )
  );
}

function isFrameFailure(value: unknown): value is FrameFailure | null {
  return (
    value === null ||
    (isRecord(value) &&
      isInteger(value.sequence) &&
      value.sequence >= 0 &&
      isDate(value.at) &&
      isOneOf(value.code, ["invalid_plant_step", "plant_step_failed", "replay_mismatch"]))
  );
}

function isReadingHistory(
  value: unknown,
  plant: PlantState,
  fields: PlantValue[],
): value is BusSample[] {
  if (!Array.isArray(value) || value.length !== Math.min(plant.sequence, 120)) return false;
  return value.every((sample: unknown, index: number) => {
    const sequence = plant.sequence - value.length + index + 1;
    return (
      isRecord(sample) &&
      isDate(sample.at) &&
      sample.sequence === sequence &&
      Date.parse(sample.at) <= Date.parse(plant.at) &&
      (index === 0 || Date.parse(sample.at) > Date.parse((value[index - 1] as BusSample).at)) &&
      (index !== value.length - 1 || sample.at === plant.at) &&
      Array.isArray(sample.values) &&
      sample.values.every(isPlantValue) &&
      sample.values.length === fields.length &&
      sample.values.every(
        (reading: PlantValue, i: number) =>
          reading.name === fields[i]?.name &&
          reading.unit === fields[i].unit &&
          (reading.at === null || Date.parse(reading.at) <= Date.parse(sample.at as string)),
      )
    );
  });
}

function isSavedRun(value: unknown): value is SavedRunInfo | null {
  return (
    value === null ||
    (isRecord(value) &&
      isDate(value.saved_at) &&
      isDate(value.simulation_at) &&
      isInteger(value.sequence) &&
      value.sequence >= 0)
  );
}

export function parseSimulationState(value: unknown): SimulationState {
  if (
    !isRecord(value) ||
    !isPlant(value.plant) ||
    !isReadingHistory(value.bus_history, value.plant, value.plant.bus) ||
    !isReadingHistory(value.battery_history, value.plant, value.plant.bess) ||
    !isPvHistory(value.pv_history, value.plant) ||
    value.plant.at !== value.at ||
    value.plant.sequence !== value.sequence ||
    typeof value.plant_trace_hash !== "string" ||
    !/^[a-f0-9]{64}$/.test(value.plant_trace_hash) ||
    !isFrameFailure(value.failure) ||
    typeof value.snapshot_saved !== "boolean" ||
    !isSavedRun(value.saved_run) ||
    !isSavedRun(value.restored_run) ||
    value.snapshot_saved !== (value.saved_run !== null) ||
    !isOneOf(value.lifecycle, ["stopped", "running", "shutdown"]) ||
    typeof value.at !== "string" ||
    !isInteger(value.sequence) ||
    !isInteger(value.trace_entries) ||
    typeof value.trace_hash !== "string" ||
    !Array.isArray(value.devices) ||
    !value.devices.every(isDevice) ||
    !Array.isArray(value.activity) ||
    !value.activity.every(isActivity) ||
    !isFleet(value.stabiliti_fleet) ||
    !isGateway(value.gateway) ||
    !Array.isArray(value.fault_rules) ||
    !value.fault_rules.every(isFaultRule) ||
    !Array.isArray(value.fault_tests) ||
    !value.fault_tests.every(isFaultTest)
  ) {
    throw new Error("Simulation API returned an invalid response");
  }
  const devices = value.devices;
  if (
    !value.plant.pv_paths.every((path) =>
      devices.some((device) => device.device_id === path.device_id),
    )
  ) {
    throw new Error("Solar path has no matching device");
  }
  return value as unknown as SimulationState;
}

async function readResponse(response: Response): Promise<SimulationState> {
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    throw new Error(
      isRecord(body) && typeof body.detail === "string"
        ? body.detail
        : `Simulation request failed (${String(response.status)})`,
    );
  }
  return parseSimulationState(await response.json());
}

export async function fetchSimulation(
  client: ApiClient,
  signal?: AbortSignal,
): Promise<SimulationState> {
  const response = await client.get(
    "/v1/simulation",
    signal === undefined ? undefined : { signal },
  );
  return readResponse(response);
}

export async function runSimulationAction(
  client: ApiClient,
  action: SimulationAction,
): Promise<SimulationState> {
  return readResponse(
    await (action === "delete"
      ? client.delete(actionPaths[action])
      : client.post(actionPaths[action])),
  );
}

export async function saveSimulationInputs(
  client: ApiClient,
  deviceId: string,
  inputs: SimulationInputs,
  generation: number,
): Promise<SimulationState> {
  const response = await client.post(
    `/v1/configuration/simulation/devices/${encodeURIComponent(deviceId)}`,
    {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        inputs,
        expected_generation: generation,
        name: "Device input changes",
        reason: "Proposed device inputs for the next simulation run.",
      }),
    },
  );
  await checkDraft(response);
  return readResponse(await client.get("/v1/simulation"));
}

export async function startSimulationFault(
  client: ApiClient,
  request: StartSimulationFault,
): Promise<SimulationState> {
  return readResponse(
    await client.post("/v1/simulation/faults", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    }),
  );
}

export async function clearSimulationFault(
  client: ApiClient,
  testId: string,
): Promise<SimulationState> {
  return readResponse(
    await client.post(`/v1/simulation/faults/${encodeURIComponent(testId)}/clear`),
  );
}

export async function fetchSimulationReport(client: ApiClient): Promise<Blob> {
  const response = await client.get("/v1/simulation/report");
  if (!response.ok) {
    throw new Error(`Simulation report failed (${String(response.status)})`);
  }
  return response.blob();
}

export async function selectSimulationProfile(
  client: ApiClient,
  profileId: string,
  seed: number,
  batteryProfile: string | null = null,
  batterySettings: BatterySettings | null = null,
  busSettings: BusSettings | null = null,
  generation = 0,
): Promise<SimulationState> {
  await checkDraft(
    await client.post("/v1/configuration/simulation/profiles", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: "Simulation setup",
        reason: "Proposed scenario, battery and DC bus settings for the next run.",
        expected_generation: generation,
        settings: {
          profile_id: profileId,
          seed,
          battery_profile: batteryProfile,
          battery_settings: batterySettings,
          bus_settings: busSettings,
        },
      }),
    }),
  );
  return readResponse(await client.get("/v1/simulation"));
}

async function checkDraft(response: Response): Promise<void> {
  if (response.ok) return;
  const body = (await response.json()) as { detail?: unknown };
  throw new Error(
    typeof body.detail === "string" ? body.detail : "Check the settings and try again.",
  );
}
