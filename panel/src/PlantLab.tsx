import {
  Button,
  ButtonSet,
  InlineNotification,
  NumberInput,
  Select,
  SelectItem,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableHeader,
  TableRow,
  Tile,
} from "@carbon/react";
import { NextOutline, Pause, Play, Repeat, Reset, Save, Undo } from "@carbon/icons-react";
import { useState, type SyntheticEvent } from "react";
import { valueLabel } from "./labels";
import { batteryNames, profileNames, pvFields } from "./api/simulation";

import type {
  BatteryConfig,
  BatterySettings,
  BusSample,
  BusSettings,
  PlantConfig,
  PlantValue,
  PvSample,
  SimulationAction,
  SimulationState,
  ValueStatus,
} from "./api/simulation";

const valueLabels: Record<ValueStatus, string> = {
  available: "Available",
  missing: "No reading",
  blocked: "Needs information",
  not_implemented: "Not included in this version",
};

const qualityLabels = {
  good: "Good",
  stale: "Stale",
  invalid: "Invalid",
  comm_error: "Communication error",
  out_of_range: "Out of range",
};

const featureLabels: Readonly<Record<string, string>> = {
  "plant.runtime": "Simulation controls",
  "plant.bus": "Shared power supply",
  "plant.balance": "Power balance",
  "plant.pv": "Solar generation",
  "plant.bess": "Battery storage",
  "plant.load": "Charging demand",
  "plant.converter": "Power converters",
  "plant.thermal": "Temperature",
};
const evidenceLabels = {
  sourced: "Reference documents",
  calibrated: "Measured data",
  provisional: "Assumptions",
  blocked: "Needs information",
  unsupported: "Not supported",
};
const detailLabels = {
  contract: "Basic behavior",
  level_1: "Power and energy",
  level_2: "Timing and limits",
  hardware_calibrated: "Equipment tested",
};
const sourceLabels: Readonly<Record<string, string>> = {
  "SRC-005": "Project topology",
  "SRC-007": "Vendor documentation response",
  "SRC-008": "Earlier control reference",
  "SRC-011": "Vendor project package",
  "SRC-016": "Battery specification reference",
  "SRC-002": "Project delivery requirements",
  "SRC-006": "Plant development plan",
  "SRC-033": "Confirmed converter roles",
  "SRC-036": "Confirmed Stabiliti power source",
};
const profileLabels: Readonly<Record<string, string>> = {
  ...profileNames,
  hil: "Hardware testing",
  production: "Live operation",
};
const profileHints: Readonly<Record<string, string>> = {
  stage0: "Check DC bus power using the device readings you enter. Solar generation is off.",
  solar_step: "See how solar power responds to sudden changes in sunlight.",
  solar_ramp: "Watch solar power rise and fall smoothly as sunlight changes.",
  solar_daylight:
    "Follow sunrise to sunset over 24 simulated hours. Starts at night, so solar power is zero at first.",
  solar_cloud: "A good starting point: see solar power drop as a cloud passes, then recover.",
  solar_variable:
    "Try uneven sunlight changes. Use the same repeat number to get the same pattern.",
};

interface ControlsProps {
  state: SimulationState;
  busy: boolean;
  run: (action: SimulationAction) => Promise<void>;
}

export function SimulationControls({ state, busy, run }: ControlsProps) {
  return (
    <>
      <ButtonSet className="lab-controls" aria-label="Simulation controls">
        <Button
          disabled={busy || state.lifecycle === "running"}
          onClick={() => void run("start")}
          renderIcon={Play}
        >
          {state.sequence > 0 ? "Continue" : "Start"}
        </Button>
        <Button
          kind="secondary"
          disabled={busy || state.lifecycle !== "running"}
          onClick={() => void run("pause")}
          renderIcon={Pause}
        >
          Pause
        </Button>
        <Button
          kind="tertiary"
          disabled={busy}
          onClick={() => void run("step")}
          renderIcon={NextOutline}
        >
          Next step
        </Button>
        <Button
          kind="tertiary"
          disabled={busy}
          onClick={() => void run("reset")}
          renderIcon={Reset}
        >
          Reset run
        </Button>
        <Button
          kind="tertiary"
          disabled={busy || state.sequence === 0}
          onClick={() => void run("replay")}
          renderIcon={Repeat}
        >
          Repeat steps
        </Button>
        <Button
          kind="tertiary"
          disabled={busy}
          onClick={() => void run("snapshot")}
          renderIcon={Save}
        >
          Save run
        </Button>
        <Button
          kind="tertiary"
          disabled={busy || !state.snapshot_saved || state.lifecycle === "running"}
          onClick={() => void run("restore")}
          renderIcon={Undo}
        >
          Restore saved run
        </Button>
        <Button
          kind="tertiary"
          disabled={busy || state.lifecycle === "running"}
          onClick={() => void run("new")}
        >
          New run
        </Button>
      </ButtonSet>
      <p>
        Settings are saved when applied. Run progress is saved only when you select Save run.
        Progress after that save is lost on restart.
      </p>
      {state.saved_run !== null && (
        <div>
          <p>
            Saved run: step {state.saved_run.sequence} · Simulation time:{" "}
            {state.saved_run.simulation_at}
            {" · Saved: "}
            {new Date(state.saved_run.saved_at).toLocaleString()}
          </p>
          <Button kind="danger--tertiary" disabled={busy} onClick={() => void run("delete")}>
            Delete saved run
          </Button>
        </div>
      )}
    </>
  );
}

interface PlantLabProps extends ControlsProps {
  applyProfile: (
    profile: string,
    seed: number,
    batteryProfile: string | null,
    batterySettings: BatterySettings | null,
    busSettings: BusSettings,
  ) => Promise<void>;
  download: () => Promise<void>;
}

export function PlantLab({ state, busy, run, applyProfile, download }: PlantLabProps) {
  const { plant } = state;
  const config = plant.configuration;
  const busReady = config.capabilities.some(
    (item) => item.capability_id === "plant.bus" && item.available,
  );
  const groups = [
    ["DC bus", plant.bus],
    ["Solar generation", plant.pv],
    ["Battery", plant.bess],
    ["Loads", plant.load],
    ["Converters", plant.converter],
    ["Temperature", plant.thermal],
  ] as const;
  return (
    <section className="plant-lab" aria-labelledby="plant-title" aria-busy={busy}>
      <div className="connection-heading">
        <div>
          <h2 id="plant-title">Plant Lab</h2>
          <p>See how power supply and demand affect the DC bus.</p>
        </div>
        <Button kind="secondary" disabled={busy} onClick={() => void download()}>
          Download report
        </Button>
      </div>
      <div className="summary-grid">
        <Tile>
          <span>Simulation status</span>
          <strong>{state.lifecycle === "running" ? "Running" : "Stopped"}</strong>
          <span>{state.failure === null ? "All devices run together" : "Last step failed"}</span>
        </Tile>
        <Tile>
          <span>Simulation step</span>
          <strong>{state.sequence}</strong>
          <span>
            {config.step_seconds} {config.step_seconds === 1 ? "second" : "seconds"} per step
          </span>
        </Tile>
        <Tile>
          <span>Profile</span>
          <strong>{profileLabels[config.profile_id]}</strong>
          <span>Repeat number {config.seed}</span>
        </Tile>
        <Tile>
          <span>Model detail</span>
          <strong>{busReady ? "Power balance and voltage response" : "Device test values"}</strong>
          <span>Simulation assumptions; not checked against equipment</span>
        </Tile>
      </div>
      <dl className="connection-summary">
        <div>
          <dt>Simulation time</dt>
          <dd>
            <time dateTime={plant.at}>{timeLabel(plant.at)}</time>
          </dd>
        </div>
        <div>
          <dt>Settings version</dt>
          <dd>{config.revision.replace(/^(stage0|solar)-/, "")}</dd>
        </div>
        <div>
          <dt>Model version</dt>
          <dd>{config.model_version}</dd>
        </div>
        <div>
          <dt>Saved run</dt>
          <dd>
            {state.saved_run === null ? "No saved run" : `Step ${String(state.saved_run.sequence)}`}
          </dd>
        </div>
      </dl>
      <SimulationControls state={state} busy={busy} run={run} />
      <ProfileForm
        key={`${config.profile_id}:${String(config.seed)}:${JSON.stringify(config.battery)}:${JSON.stringify(config.bus)}`}
        profile={config.profile_id}
        batteryConfig={config.battery}
        busConfig={config.bus}
        initialSeed={config.seed}
        disabled={busy || state.lifecycle === "running"}
        apply={applyProfile}
      />
      <InlineNotification
        kind="info"
        title="Available in this version"
        subtitle={
          config.battery !== null
            ? "Battery energy, level, power limits and losses are calculated from actual bus transfer using the selected test settings."
            : config.pv.length > 0
              ? "Solar generation, conversion loss and unused power are available alongside the DC bus model. These calculations use simulation assumptions."
              : busReady
                ? "DC bus energy, voltage and power balance are available. Choose a solar profile to include solar generation."
                : "Shared controls and device test readings are available."
        }
        hideCloseButton
      />
      {plant.balance === "missing" && (
        <InlineNotification
          kind="warning"
          title="Waiting for power readings"
          subtitle="Enter charging demand and the power needed from the DC bus for each auxiliary converter. Missing or excluded readings are listed in the power table. The bus calculation pauses until all required readings are usable."
          hideCloseButton
        />
      )}
      <div className="plant-sections">
        {groups.map(([label, readings]) => (
          <Tile key={label}>
            <h3>{label}</h3>
            {label === "Battery" ? (
              config.battery === null ? (
                <p>Select a Battery setup above to calculate stored energy and battery level.</p>
              ) : (
                <>
                  <p>Direction: {batteryDirection(readings)}</p>
                  <p>
                    What is limiting power?{" "}
                    {readings.find((value) => value.name === "Actual power")?.reason}
                  </p>
                  <Readings
                    values={batteryReadings(readings).filter((value) =>
                      [
                        "Stored energy",
                        "Battery level",
                        "Actual power",
                        "Energy loss rate",
                      ].includes(value.name),
                    )}
                  />
                </>
              )
            ) : label === "Solar generation" && config.pv.length === 0 ? (
              <>
                <p>Select a solar profile to see solar generation.</p>
                <p>
                  Choose <strong>Passing cloud</strong> above to watch solar power drop and recover.
                  Then select <strong>Save for review</strong>.
                </p>
              </>
            ) : label === "Solar generation" ? (
              <>
                <Readings
                  values={readings.filter((reading) =>
                    [
                      "Available solar",
                      "Power delivered",
                      "Conversion loss",
                      "Unused solar",
                    ].includes(reading.name),
                  )}
                />
                <details>
                  <summary>All solar totals</summary>
                  <Readings values={readings} />
                </details>
              </>
            ) : (
              <Readings values={readings} />
            )}
          </Tile>
        ))}
      </div>
      <Tile className="bus-balance">
        <h3>Power balance</h3>
        <p>
          {plant.balance === "available"
            ? "All required power readings are included."
            : "A complete balance is not available yet."}
        </p>
        <Readings values={plant.ledger} />
        <details>
          <summary>Bus settings</summary>
          <p>Simulation assumptions; these are not approved equipment limits.</p>
          <dl className="connection-summary">
            {[
              ["Starting voltage", `${formatNumber(config.bus.nominal_voltage_v)} VDC`],
              ["Simulation bounds", `0 to ${formatNumber(config.bus.max_voltage_v)} VDC`],
              ["Bus capacitance", `${formatNumber(config.bus.capacitance_f)} F`],
              ["Bus loss", `${formatNumber(config.bus.loss_ratio * 100)}%`],
              ["Allowed balance error", `${formatNumber(config.bus.tolerance_kw)} kW`],
            ].map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        </details>
      </Tile>
      <TableContainer
        title="Power by device"
        description="Positive requested and accepted power feeds the DC bus; negative power draws from it. Battery transfer is counted at the battery converter only."
        className="bus-table"
      >
        <Table size="sm">
          <TableHead>
            <TableRow>
              {[
                "Device",
                "Requested (kW)",
                "Accepted (kW)",
                "Supplied (kW)",
                "Used (kW)",
                "Details",
              ].map((label) => (
                <TableHeader key={label}>{label}</TableHeader>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {plant.ports
              .filter((port) => port.device_id !== "ba1-1")
              .map((port) => (
                <TableRow key={port.device_id}>
                  <TableCell>
                    {
                      state.devices.find((device) => device.device_id === port.device_id)
                        ?.display_name
                    }
                  </TableCell>
                  {["Requested power", "Accepted power", "Power supplied", "Power used"].map(
                    (name) => {
                      const reading = port.ledger.find((value) => value.name === name);
                      return (
                        <TableCell key={name}>
                          {reading?.value == null ? "No reading" : formatNumber(reading.value)}
                        </TableCell>
                      );
                    },
                  )}
                  <TableCell>
                    <details>
                      <summary>Power details</summary>
                      <Readings values={port.ledger} />
                    </details>
                  </TableCell>
                </TableRow>
              ))}
          </TableBody>
        </Table>
      </TableContainer>
      <ReadingHistory
        samples={state.bus_history}
        name="Bus voltage"
        unit="VDC"
        id="bus"
        title="DC bus voltage history"
        tableLabel="View voltage readings"
        description="The latest 120 simulation steps. Gaps mean the bus calculation was paused; skipped time is not reconstructed."
      />
      {plant.pv_paths.length > 0 && (
        <>
          <TableContainer
            title="Solar power by device"
            description="Solar power enters the DC bus through CNVTR-2."
            className="bus-table"
          >
            <Table size="sm">
              <TableHead>
                <TableRow>
                  {[
                    "Device",
                    "Available (kW)",
                    "Requested (kW)",
                    "Accepted (kW)",
                    "Delivered (kW)",
                    "Loss (kW)",
                    "Unused (kW)",
                    "Details",
                  ].map((label) => (
                    <TableHeader key={label}>{label}</TableHeader>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {plant.pv_paths.map((path) => (
                  <TableRow key={path.device_id}>
                    <TableCell>
                      {
                        state.devices.find((device) => device.device_id === path.device_id)
                          ?.display_name
                      }
                      <br />
                      DC bus
                    </TableCell>
                    {[0, 1, 2, 5, 6, 7].map((index) => (
                      <TableCell key={index}>
                        {path.values[index]?.value == null
                          ? "No reading"
                          : formatNumber(path.values[index].value)}
                      </TableCell>
                    ))}
                    <TableCell>
                      <details>
                        <summary>Solar details</summary>
                        <p>{path.reason}</p>
                        <p>Device status: {path.health}</p>
                        <Readings values={path.values} />
                      </details>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
          <details className="bus-table solar-settings">
            <summary>Solar settings and assumptions</summary>
            <p>
              Available solar equals power delivered, conversion loss and unused solar. Solar loss
              is counted before the DC bus. Unused solar includes any supply the bus could not
              accept.
            </p>
            <p>
              Stabiliti units draw power from the DC bus to supply AC loads. They have no separate
              solar input. Calculated AC output assumes ideal conversion with no loss.
            </p>
            <Table size="sm">
              <TableHead>
                <TableRow>
                  {["Device", "Conversion efficiency", "Output limit (kW)", "Sunlight samples"].map(
                    (label) => (
                      <TableHeader key={label}>{label}</TableHeader>
                    ),
                  )}
                </TableRow>
              </TableHead>
              <TableBody>
                {config.pv.map((path) => (
                  <TableRow key={path.device_id}>
                    <TableCell>
                      {
                        state.devices.find((device) => device.device_id === path.device_id)
                          ?.display_name
                      }
                    </TableCell>
                    <TableCell>{formatNumber(path.efficiency * 100)}%</TableCell>
                    <TableCell>{path.limit_kw}</TableCell>
                    <TableCell>
                      <details>
                        <summary>View sunlight profile</summary>
                        <p>
                          {path.interpolation === "linear"
                            ? "Linear change between samples"
                            : "Hold until the next sample"}
                          . Times start at zero; the last value is held.
                        </p>
                        <Table size="sm">
                          <TableHead>
                            <TableRow>
                              <TableHeader>Elapsed seconds</TableHeader>
                              <TableHeader>Available solar (kW)</TableHeader>
                            </TableRow>
                          </TableHead>
                          <TableBody>
                            {path.points.map(([seconds, power]) => (
                              <TableRow key={seconds}>
                                <TableCell>{seconds}</TableCell>
                                <TableCell>{formatNumber(power)}</TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      </details>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </details>
          <SolarHistory samples={state.pv_history} />
        </>
      )}
      {config.battery !== null && (
        <section className="battery-storage" aria-label="Battery storage details">
          <h3>Battery storage</h3>
          <p>
            {batteryNames[config.battery.profile_id]}. Calculated battery level comes from stored
            energy; reported device readings remain separate test inputs.
          </p>
          <p>
            Use the battery converter controls to enter requested power: positive supplies the bus,
            negative charges the battery. Charging needs available bus power; discharging needs a
            load or space in the bus.
          </p>
          <Readings values={batteryReadings(plant.bess)} />
          <details>
            <summary>Battery settings and assumptions</summary>
            <p>
              Simulation settings; installed capacity is unresolved. All power limits refer to the
              DC bus. Efficiency covers the battery path once. Idle self-discharge and
              self-consumption are not modeled.
            </p>
            <dl className="connection-summary">
              {[
                ["Usable capacity", `${formatNumber(config.battery.capacity_kwh)} kWh`],
                ["Starting energy", `${formatNumber(config.battery.initial_energy_kwh)} kWh`],
                ["Charge efficiency", `${formatNumber(config.battery.charge_efficiency * 100)}%`],
                [
                  "Discharge efficiency",
                  `${formatNumber(config.battery.discharge_efficiency * 100)}%`,
                ],
                ["Charge power bound", `${formatNumber(config.battery.charge_limit_kw)} kW`],
                ["Discharge power bound", `${formatNumber(config.battery.discharge_limit_kw)} kW`],
                ["Model detail", "Power and energy"],
              ].map(([name, value]) => (
                <div key={name}>
                  <dt>{name}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
          </details>
          <BatteryHistory samples={state.battery_history} />
        </section>
      )}
      <TableContainer
        title="Connected devices"
        description="All seven devices update together. Expand a row to see its readings and shared plant values."
      >
        <Table size="sm" className="plant-table">
          <TableHead>
            <TableRow>
              {["Device", "Step", "Status", "Device readings", "Shared plant values"].map(
                (label) => (
                  <TableHeader key={label}>{label}</TableHeader>
                ),
              )}
            </TableRow>
          </TableHead>
          <TableBody>
            {plant.ports.map((port) => (
              <TableRow key={port.device_id}>
                <TableCell>
                  {state.devices.find((device) => device.device_id === port.device_id)
                    ?.display_name ?? port.device_id}
                </TableCell>
                <TableCell>{port.sequence}</TableCell>
                <TableCell>{port.health.charAt(0).toUpperCase() + port.health.slice(1)}</TableCell>
                <TableCell>
                  <details>
                    <summary>{port.readings.length} reported values</summary>
                    <Readings values={port.readings} />
                  </details>
                </TableCell>
                <TableCell>
                  <details>
                    <summary>{port.outputs.length} shared readings</summary>
                    <Readings values={port.outputs} />
                  </details>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
      <TableContainer
        title="Available features"
        description="Availability shows what you can use now. Model information contains the supporting references and review status."
      >
        <Table size="sm" className="plant-table">
          <TableHead>
            <TableRow>
              {["Feature", "Status", "Details"].map((label) => (
                <TableHeader key={label}>{label}</TableHeader>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {config.capabilities.map((item) => (
              <TableRow key={item.capability_id}>
                <TableCell>{featureLabels[item.capability_id] ?? "Other feature"}</TableCell>
                <TableCell>
                  {item.available
                    ? "Ready"
                    : item.capability_id === "plant.pv"
                      ? "Available with a solar profile"
                      : "Unavailable in this version"}
                </TableCell>
                <TableCell>
                  <details>
                    <summary>Model information</summary>
                    <p>Basis: {evidenceLabels[item.evidence_classification]}</p>
                    <p>Model detail: {detailLabels[item.fidelity_level]}</p>
                    <p>
                      Review:{" "}
                      {item.review_status === "pending"
                        ? "Not independently reviewed yet"
                        : item.review_status}
                    </p>
                    <p>
                      Review owner: {config.review_owner}. Reviewers: {config.reviewers.join(", ")}.
                    </p>
                    {item.enabled_profiles.length > 0 && (
                      <p>
                        Available for:{" "}
                        {item.enabled_profiles
                          .map((profile) => profileLabels[profile] ?? profile)
                          .join(", ")}
                      </p>
                    )}
                    {item.source_ids.length > 0 && (
                      <p>
                        References: {item.source_ids.map((id) => sourceLabels[id] ?? id).join(", ")}
                      </p>
                    )}
                    {item.decision_ids.length > 0 && (
                      <p>Decisions: {item.decision_ids.join(", ")}</p>
                    )}
                    <ul>
                      {item.assumptions.map((note) => (
                        <li key={note}>{note}</li>
                      ))}
                    </ul>
                    {item.calibration_evidence !== null && (
                      <p>Equipment checks: {item.calibration_evidence}</p>
                    )}
                    {item.calibration_tolerance !== null && (
                      <p>Allowed difference: {item.calibration_tolerance}</p>
                    )}
                  </details>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
      <Tile>
        <h3>What this simulation can show</h3>
        <ul>
          {config.limitations.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
        <details>
          <summary>Run verification</summary>
          <p>Use this value to compare the plant history with a saved report.</p>
          <code className="plant-hash">{state.plant_trace_hash}</code>
        </details>
      </Tile>
    </section>
  );
}

function Readings({ values }: { values: PlantValue[] }) {
  if (values.length === 0) return <p>No values reported yet.</p>;
  if (values.every((value) => value.status === "not_implemented"))
    return <p>{valueLabels.not_implemented}.</p>;
  return (
    <dl className="plant-readings">
      {values.map((reading) => (
        <div key={`${reading.capability_id}:${reading.name}`}>
          <dt>{reading.name.includes(".") ? valueLabel(reading.name) : reading.name}</dt>
          <dd>
            <span>
              {reading.value === null
                ? valueLabels[reading.status]
                : `${formatNumber(reading.value)} ${reading.unit}`}
            </span>
            {reading.quality !== null && <span>Quality: {qualityLabels[reading.quality]}</span>}
            {reading.status !== "not_implemented" && <small>{reading.reason}</small>}
            {reading.at !== null && <time dateTime={reading.at}>{timeLabel(reading.at)}</time>}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function ProfileForm({
  profile,
  initialSeed,
  batteryConfig,
  busConfig,
  disabled,
  apply,
}: {
  profile: string;
  batteryConfig: BatteryConfig | null;
  busConfig: PlantConfig["bus"];
  initialSeed: number;
  disabled: boolean;
  apply: (
    profile: string,
    seed: number,
    batteryProfile: string | null,
    batterySettings: BatterySettings | null,
    busSettings: BusSettings,
  ) => Promise<void>;
}) {
  const [seed, setSeed] = useState<number | string>(initialSeed);
  const [selected, setSelected] = useState(profile);
  const [battery, setBattery] = useState(batteryConfig?.profile_id ?? "");
  const [bus, setBus] = useState<Record<keyof BusSettings, number | string>>({
    nominal_voltage_v: busConfig.nominal_voltage_v,
    max_voltage_v: busConfig.max_voltage_v,
    capacitance_f: busConfig.capacitance_f,
    loss_percent: busConfig.loss_ratio * 100,
    tolerance_kw: busConfig.tolerance_kw,
  });
  const busFields: { key: keyof BusSettings; label: string; hint: string; step: number }[] = [
    {
      key: "nominal_voltage_v",
      label: "Starting voltage (VDC)",
      hint: "Voltage at the start of the run. It can change as the simulation runs.",
      step: 1,
    },
    {
      key: "max_voltage_v",
      label: "Maximum simulated voltage (VDC)",
      hint: "Model ceiling, not an equipment trip setting. Must be at least the starting voltage.",
      step: 1,
    },
    {
      key: "capacitance_f",
      label: "Bus capacitance (F)",
      hint: "Size of the bus energy buffer. A larger value slows voltage changes.",
      step: 0.1,
    },
    {
      key: "loss_percent",
      label: "Bus loss (%)",
      hint: "Additional loss inside the bus. Enter zero or a value below 100.",
      step: 0.1,
    },
    {
      key: "tolerance_kw",
      label: "Calculation tolerance (kW)",
      hint: "Allowed rounding error in power accounting, not an allowed supply shortage.",
      step: 0.000001,
    },
  ];
  function busValid(key: keyof BusSettings): boolean {
    const value = Number(bus[key]);
    if (bus[key] === "" || !Number.isFinite(value)) return false;
    if (key === "loss_percent") return value >= 0 && value < 100;
    if (value <= 0) return false;
    return key !== "max_voltage_v" || value >= Number(bus.nominal_voltage_v);
  }
  const [values, setValues] = useState<Record<keyof BatterySettings, number | string>>({
    capacity_kwh: batteryConfig?.capacity_kwh ?? 3500,
    initial_level_percent:
      batteryConfig === null
        ? 50
        : (batteryConfig.initial_energy_kwh / batteryConfig.capacity_kwh) * 100,
    charge_efficiency_percent: (batteryConfig?.charge_efficiency ?? 0.95) * 100,
    discharge_efficiency_percent: (batteryConfig?.discharge_efficiency ?? 0.95) * 100,
    charge_limit_kw: batteryConfig?.charge_limit_kw ?? 100,
    discharge_limit_kw: batteryConfig?.discharge_limit_kw ?? 100,
  });
  const fields: { key: keyof BatterySettings; label: string; max?: number; positive?: boolean }[] =
    [
      { key: "capacity_kwh", label: "Usable capacity (kWh)", positive: true },
      { key: "initial_level_percent", label: "Starting battery level (%)", max: 100 },
      {
        key: "charge_efficiency_percent",
        label: "Charge efficiency (%)",
        max: 100,
        positive: true,
      },
      {
        key: "discharge_efficiency_percent",
        label: "Discharge efficiency (%)",
        max: 100,
        positive: true,
      },
      { key: "charge_limit_kw", label: "Maximum charge power (kW)" },
      { key: "discharge_limit_kw", label: "Maximum discharge power (kW)" },
    ];
  function fieldValid(field: (typeof fields)[number]): boolean {
    const value = Number(values[field.key]);
    return (
      values[field.key] !== "" &&
      Number.isFinite(value) &&
      (field.positive ? value > 0 : value >= 0) &&
      (field.max === undefined || value <= field.max)
    );
  }
  const number = seed === "" ? Number.NaN : Number(seed);
  const validSeed = Number.isSafeInteger(number) && number >= 0 && number <= 2147483647;
  const valid =
    validSeed &&
    (battery === "" || fields.every(fieldValid)) &&
    busFields.every((field) => busValid(field.key));
  function submit(event: SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (disabled || !valid) return;
    const settings: BatterySettings | null =
      battery === "custom"
        ? {
            capacity_kwh: Number(values.capacity_kwh),
            initial_level_percent: Number(values.initial_level_percent),
            charge_efficiency_percent: Number(values.charge_efficiency_percent),
            discharge_efficiency_percent: Number(values.discharge_efficiency_percent),
            charge_limit_kw: Number(values.charge_limit_kw),
            discharge_limit_kw: Number(values.discharge_limit_kw),
          }
        : null;
    void apply(selected, number, battery === "" ? null : battery, settings, {
      nominal_voltage_v: Number(bus.nominal_voltage_v),
      max_voltage_v: Number(bus.max_voltage_v),
      capacitance_f: Number(bus.capacitance_f),
      loss_percent: Number(bus.loss_percent),
      tolerance_kw: Number(bus.tolerance_kw),
    });
  }
  return (
    <form className="plant-profile" onSubmit={submit} noValidate>
      <Select
        id="plant-profile"
        labelText="Profile"
        helperText={profileHints[selected]}
        value={selected}
        disabled={disabled}
        onChange={(event) => {
          setSelected(event.target.value);
        }}
      >
        {Object.entries(profileNames).map(([id, label]) => (
          <SelectItem key={id} value={id} text={label} />
        ))}
      </Select>
      <Select
        id="battery-setup"
        labelText="Battery setup"
        helperText="Choose a preset or enter custom test settings below."
        value={battery}
        disabled={disabled}
        onChange={(event) => {
          const choice = event.target.value;
          setBattery(choice);
          const capacities: Record<string, number> = {
            battery_2700: 2700,
            battery_3500: 3500,
            battery_3850: 3850,
          };
          const capacity = capacities[choice];
          if (capacity !== undefined)
            setValues({
              capacity_kwh: capacity,
              initial_level_percent: 50,
              charge_efficiency_percent: 95,
              discharge_efficiency_percent: 95,
              charge_limit_kw: 100,
              discharge_limit_kw: 100,
            });
        }}
      >
        <SelectItem value="" text="Not included" />
        {Object.entries(batteryNames).map(([id, label]) => (
          <SelectItem key={id} value={id} text={label} />
        ))}
      </Select>
      <NumberInput
        id="plant-seed"
        label="Repeat number"
        min={0}
        max={2147483647}
        step={1}
        value={seed}
        allowEmpty
        disabled={disabled}
        invalid={!validSeed}
        invalidText="Enter a whole number from 0 to 2147483647."
        onChange={(_, data) => {
          setSeed(data.value);
        }}
      />
      {battery !== "" && (
        <>
          <p>Battery settings — values for this simulation, not confirmed equipment ratings.</p>
          {fields.map((field) => (
            <NumberInput
              key={field.key}
              id={`battery-${field.key}`}
              label={field.label}
              min={0}
              {...(field.max === undefined ? {} : { max: field.max })}
              step={1}
              value={values[field.key]}
              allowEmpty
              disabled={disabled}
              invalid={!fieldValid(field)}
              invalidText={
                field.max === 100
                  ? field.positive
                    ? "Enter a value above 0 and up to 100."
                    : "Enter a value from 0 to 100."
                  : field.positive
                    ? "Enter a number greater than 0."
                    : "Enter zero or a positive number."
              }
              onChange={(_, data) => {
                setValues({ ...values, [field.key]: data.value });
                setBattery("custom");
              }}
            />
          ))}
        </>
      )}
      <p>Bus settings — starting conditions for this simulation. The minimum voltage is zero.</p>
      {busFields.map((field) => (
        <NumberInput
          key={field.key}
          id={`bus-${field.key}`}
          label={field.label}
          helperText={field.hint}
          min={0}
          step={field.step}
          value={bus[field.key]}
          allowEmpty
          disabled={disabled}
          invalid={!busValid(field.key)}
          invalidText={
            field.key === "loss_percent"
              ? "Enter zero or a value below 100."
              : field.key === "max_voltage_v"
                ? "Enter a positive voltage at least as high as the starting voltage."
                : "Enter a number greater than 0."
          }
          onChange={(_, data) => {
            setBus({ ...bus, [field.key]: data.value });
          }}
        />
      ))}
      <Button type="submit" kind="secondary" disabled={disabled || !valid}>
        Save for review
      </Button>
      <p>
        Saving creates a proposal. An administrator can approve and apply it in Settings history.
        Applying starts a new paused run with the selected scenario’s default device inputs and
        retains your saved run.
      </p>
    </form>
  );
}

function timeLabel(at: string): string {
  return `${new Date(at).toLocaleString("en-GB", { timeZone: "UTC" })} UTC`;
}

function formatNumber(value: number, digits = 6): string {
  return new Intl.NumberFormat("en-GB", { maximumFractionDigits: digits }).format(value);
}

function ReadingHistory({
  samples,
  name,
  unit,
  id,
  title,
  tableLabel,
  description,
}: {
  samples: BusSample[];
  name: string;
  unit: string;
  id: string;
  title: string;
  tableLabel: string;
  description: string;
}) {
  const readings = samples.map((sample) => {
    const reading = sample.values.find((value) => value.name === name);
    const value = reading?.quality === "good" && reading.at === sample.at ? reading.value : null;
    return { ...sample, reading, value };
  });
  const numbers = readings.flatMap((sample) => (sample.value === null ? [] : [sample.value]));
  const low = numbers.length ? Math.min(...numbers) : 0;
  const high = numbers.length ? Math.max(...numbers) : 0;
  const margin = Math.max(high - low, 1) * 0.1;
  const first = samples.at(0);
  const last = samples.at(-1);
  const start = first ? Date.parse(first.at) : 0;
  const end = last ? Date.parse(last.at) : start;
  const duration = Math.max(end - start, 1);
  let segment: { x: number; y: number }[] = [];
  const segments = [segment];
  for (const sample of readings) {
    if (sample.value === null) {
      segment = [];
      segments.push(segment);
      continue;
    }
    const x = 75 + ((Date.parse(sample.at) - start) / duration) * 545;
    const y = 150 - ((sample.value - low + margin) / (high - low + 2 * margin)) * 125;
    segment.push({ x, y });
  }
  return (
    <TableContainer title={title} description={description} className="bus-history">
      {samples.length === 0 ? (
        <p>Take a step to start the history.</p>
      ) : numbers.length === 0 ? (
        <p>No calculated readings in this history.</p>
      ) : (
        <svg viewBox="0 0 650 205" role="img" aria-labelledby={`${id}-chart-title`}>
          <title id={`${id}-chart-title`}>
            {name === "Bus voltage" ? "DC bus voltage" : name} over simulation time. Exact readings
            and quality are in the table below.
          </title>
          <path className="bus-axis" d="M75 20 V150 H620" />
          <text x="75" y="15">
            {name === "Bus voltage" ? "Voltage" : name} ({unit})
          </text>
          <text x="65" y="29" textAnchor="end">
            {formatNumber(high + margin, 2)}
          </text>
          <text x="65" y="150" textAnchor="end">
            {formatNumber(low - margin, 2)}
          </text>
          <text x="75" y="172">
            0
          </text>
          <text x="620" y="172" textAnchor="end">
            {formatNumber((end - start) / 1000)}
          </text>
          <text x="345" y="195" textAnchor="middle">
            Elapsed simulation time (seconds)
          </text>
          {segments.map((points) => {
            const point = points[0];
            if (!point) return null;
            return points.length === 1 ? (
              <circle key={point.x} className="bus-point" cx={point.x} cy={point.y} r="3" />
            ) : (
              <polyline
                key={point.x}
                className="bus-line"
                points={points.map((item) => `${String(item.x)},${String(item.y)}`).join(" ")}
              />
            );
          })}
        </svg>
      )}
      <details>
        <summary>{tableLabel}</summary>
        <Table size="sm">
          <TableHead>
            <TableRow>
              {[
                "Step",
                "Simulation time",
                `${name === "Bus voltage" ? "Voltage" : name} (${unit})`,
                "Reading time",
                "Quality",
              ].map((label) => (
                <TableHeader key={label}>{label}</TableHeader>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {[...readings].reverse().map((sample) => (
              <TableRow key={sample.sequence}>
                <TableCell>{sample.sequence}</TableCell>
                <TableCell>
                  <time dateTime={sample.at}>{timeLabel(sample.at)}</time>
                </TableCell>
                <TableCell>
                  {sample.reading?.value == null
                    ? "No reading"
                    : formatNumber(sample.reading.value)}
                </TableCell>
                <TableCell>
                  {sample.reading?.at == null ? (
                    "No reading"
                  ) : (
                    <time dateTime={sample.reading.at}>{timeLabel(sample.reading.at)}</time>
                  )}
                </TableCell>
                <TableCell>
                  {sample.reading?.quality === "good" ? "Calculated" : "Calculation paused"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </details>
    </TableContainer>
  );
}

function SolarHistory({ samples }: { samples: PvSample[] }) {
  const [name, setName] = useState("Power delivered");
  return (
    <section className="solar-history" aria-label="Solar history controls">
      <Select
        id="solar-history-value"
        labelText="Solar measurement"
        value={name}
        onChange={(event) => {
          setName(event.target.value);
        }}
      >
        {pvFields.map((field) => (
          <SelectItem key={field} value={field} text={field} />
        ))}
      </Select>
      <ReadingHistory
        samples={samples}
        name={name}
        unit="kW"
        id="solar"
        title="Solar power history"
        tableLabel="View solar readings"
        description="The latest 120 simulation steps for CNVTR-2. Gaps mean readings are unavailable."
      />
    </section>
  );
}

function batteryReadings(values: PlantValue[]): PlantValue[] {
  return values.map((value) => {
    if (value.name === "Battery SOC")
      return {
        ...value,
        name: "Battery level",
        unit: "%",
        value: value.value === null ? null : value.value * 100,
      };
    if (value.name === "Battery energy") return { ...value, name: "Stored energy" };
    return value;
  });
}

function batteryDirection(values: PlantValue[]): string {
  const actual = values.find((value) => value.name === "Actual power");
  if (actual?.value == null || actual.quality !== "good") return "Unavailable";
  return actual.value > 0 ? "Discharging" : actual.value < 0 ? "Charging" : "Idle";
}

function BatteryHistory({ samples }: { samples: BusSample[] }) {
  const [name, setName] = useState("Battery level");
  const fields: [string, string][] = [
    ["Battery level", "%"],
    ["Stored energy", "kWh"],
    ["Requested power", "kW"],
    ["Accepted power", "kW"],
    ["Actual power", "kW"],
    ["Charge limit", "kW"],
    ["Discharge limit", "kW"],
    ["Energy loss rate", "kW"],
  ];
  const unit = name === "Battery level" ? "%" : name === "Stored energy" ? "kWh" : "kW";
  return (
    <>
      <Select
        id="battery-history-value"
        labelText="Battery measurement"
        value={name}
        onChange={(event) => {
          setName(event.target.value);
        }}
      >
        {fields.map(([field]) => (
          <SelectItem key={field} value={field} text={field} />
        ))}
      </Select>
      <ReadingHistory
        samples={samples.map((sample) => ({ ...sample, values: batteryReadings(sample.values) }))}
        name={name}
        unit={unit}
        id="battery-history"
        title="Battery history"
        tableLabel="View battery readings"
        description="Calculated from actual charging and discharging. Gaps show unavailable calculations."
      />
    </>
  );
}
