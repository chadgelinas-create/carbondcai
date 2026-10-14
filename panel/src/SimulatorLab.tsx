import {
  Button,
  ButtonSet,
  DataTableSkeleton,
  InlineNotification,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableHeader,
  TableRow,
  Tile,
} from "@carbon/react";
import { useEffect, useRef, useState, type ReactNode, type SyntheticEvent } from "react";

import type { ApiClient } from "./api/client";
import {
  clearSimulationFault,
  fetchSimulation,
  fetchSimulationReport,
  runSimulationAction,
  saveSimulationInputs,
  selectSimulationProfile,
  startSimulationFault,
  type BatteryInputs,
  type BatterySettings,
  type BusSettings,
  type ConverterInputs,
  type Dc600Inputs,
  type SimulationAction,
  type SimulationDevice,
  type SimulationFaultRule,
  type SimulationGateway,
  type SimulationInputs,
  type StartSimulationFault,
  type SimulationState,
  type SimulationValue,
  type StabilitiInputs,
} from "./api/simulation";

import { PlantLab, SimulationControls } from "./PlantLab";
import { title, valueLabel } from "./labels";

type ViewState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "loaded"; state: SimulationState };
type LabView = "plant" | "overview" | "devices" | "faults" | "activity";
type BusyState = SimulationAction | "profile" | "inputs" | "fault" | "report" | null;

const headers = ["Device", "Health", "Simulation", "Test connection", "Action"];

const actionMessages: Record<SimulationAction, string> = {
  start: "Started.",
  pause: "Paused.",
  step: "One step complete.",
  reset: "Reset complete.",
  replay: "Steps repeated.",
  snapshot: "Run saved. You can restore it after a server restart.",
  restore: "Saved run restored. Paused.",
  new: "New run ready. Paused. The saved checkpoint is unchanged.",
  delete: "Saved run deleted. Settings and the current run are unchanged.",
};

const viewLabels: Record<LabView, string> = {
  overview: "Overview",
  plant: "Plant Lab",
  devices: "Devices",
  faults: "Fault testing",
  activity: "Activity",
};

function gatewayLabel(gateway: SimulationGateway): string {
  if (gateway.state === "ready") return "Ready for connections";
  if (gateway.state === "problem") return "Connection problem";
  return "Connection not running";
}

function clientLabel(clients: number): string {
  if (clients === 0) return "No client connected";
  return clients === 1 ? "1 client connected" : `${String(clients)} clients connected`;
}

function connectionLabel(device: SimulationDevice, gateway: SimulationGateway): string {
  if (!gateway.routes.some((route) => route.device_id === device.device_id)) {
    return "Built-in simulator";
  }
  return gateway.state === "ready" ? "Ready for Modbus testing" : "Connection not running";
}

function roleLabel(device: SimulationDevice): string {
  if (device.device_id === "cnvtr-1") return "Battery-side converter";
  if (device.device_id === "cnvtr-2") return "Solar-side converter";
  if (device.device_id.startsWith("st-")) return "Stabiliti power converter";
  if (device.device_id === "dc600") return "EV charging demand and available power";
  return device.role;
}

function deviceReference(device: SimulationDevice): string {
  const references: Readonly<Record<string, string>> = {
    "st-4": "Unit 01",
    "st-5": "Unit 02",
    "st-6": "Unit 03",
  };
  return references[device.device_id] ?? device.device_id;
}

function PageTitle() {
  return (
    <div className="page-title">
      <p>Simulation only</p>
      <h1 id="simulator-title">Simulator Lab</h1>
      <p>This page uses simulated devices. It is not connected to physical equipment.</p>
    </div>
  );
}

export function SimulatorLab({ client }: { client: ApiClient }) {
  const [view, setView] = useState<ViewState>({ kind: "loading" });
  const [labView, setLabView] = useState<LabView>("overview");
  const [selectedDevice, setSelectedDevice] = useState<string | null>(null);
  const [busy, setBusy] = useState<BusyState>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [offline, setOffline] = useState(false);
  const [offlineMessage, setOfflineMessage] = useState("");
  const [reload, setReload] = useState(0);
  const pendingRead = useRef<AbortController | null>(null);
  const actionRunning = useRef(false);
  const wasOffline = useRef(false);

  function load(): void {
    setReload((value) => value + 1);
  }

  function beginAction(action: BusyState): boolean {
    if (actionRunning.current) return false;
    actionRunning.current = true;
    pendingRead.current?.abort();
    setBusy(action);
    setActionError(null);
    setMessage("");
    return true;
  }

  function endAction(): void {
    actionRunning.current = false;
    setBusy(null);
    setReload((value) => value + 1);
  }

  useEffect(() => {
    if (busy !== null) return;
    const request = new AbortController();
    pendingRead.current = request;
    const active = (): boolean => !request.signal.aborted;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh(): Promise<void> {
      if (!active() || actionRunning.current) return;
      try {
        const state = await fetchSimulation(client, request.signal);
        if (active()) {
          setView({ kind: "loaded", state });
          setOffline(false);
          if (wasOffline.current)
            setMessage("Connection restored. Current simulation state is shown.");
          wasOffline.current = false;
        }
      } catch (error) {
        if (active()) {
          setOffline(true);
          setOfflineMessage(
            error instanceof Error ? error.message : "The simulator is unavailable.",
          );
          wasOffline.current = true;
          setView((current) =>
            current.kind === "loaded"
              ? current
              : {
                  kind: "error",
                  message:
                    error instanceof Error ? error.message : "The device list is unavailable.",
                },
          );
        }
      } finally {
        if (active()) timer = setTimeout(() => void refresh(), 1000);
      }
    }
    void refresh();
    return () => {
      clearTimeout(timer);
      request.abort();
    };
  }, [client, busy, reload]);

  async function run(action: SimulationAction): Promise<void> {
    if (!beginAction(action)) return;
    try {
      const state = await runSimulationAction(client, action);
      setView({ kind: "loaded", state });
      setOffline(false);
      setMessage(
        state.failure === null
          ? actionMessages[action]
          : "The step could not finish. Your last complete results are still shown.",
      );
      if (state.failure === null && ["reset", "restore", "replay", "new"].includes(action))
        setSelectedDevice(null);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "The action could not finish.");
    } finally {
      endAction();
    }
  }

  async function save(deviceId: string, inputs: SimulationInputs): Promise<void> {
    if (!beginAction("inputs")) return;
    try {
      const state = await saveSimulationInputs(
        client,
        deviceId,
        inputs,
        view.kind === "loaded" ? (view.state.configuration_generation ?? 0) : 0,
      );
      setView({ kind: "loaded", state });
      setMessage(
        "Device inputs saved for review. Open Settings history to approve and apply them.",
      );
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "The action could not finish.");
    } finally {
      endAction();
    }
  }

  async function startFault(request: StartSimulationFault): Promise<void> {
    if (!beginAction("fault")) return;
    try {
      const state = await startSimulationFault(client, request);
      setView({ kind: "loaded", state });
      setMessage("Fault test scheduled. Run the next step when you are ready.");
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "The action could not finish.");
    } finally {
      endAction();
    }
  }

  async function clearFault(testId: string): Promise<void> {
    if (!beginAction("fault")) return;
    try {
      const state = await clearSimulationFault(client, testId);
      setView({ kind: "loaded", state });
      setMessage("Fault test cleared. Run the next step to confirm recovery.");
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "The action could not finish.");
    } finally {
      endAction();
    }
  }

  async function downloadReport(): Promise<void> {
    if (!beginAction("report")) return;
    try {
      const report = await fetchSimulationReport(client);
      const reportUrl = URL.createObjectURL(report);
      const link = document.createElement("a");
      link.href = reportUrl;
      link.download = "simulator-report.json";
      link.click();
      URL.revokeObjectURL(reportUrl);
      setMessage("Report downloaded.");
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "The action could not finish.");
    } finally {
      endAction();
    }
  }

  async function applyProfile(
    profile: string,
    seed: number,
    batteryProfile: string | null,
    batterySettings: BatterySettings | null,
    busSettings: BusSettings,
  ): Promise<void> {
    if (!beginAction("profile")) return;
    try {
      const state = await selectSimulationProfile(
        client,
        profile,
        seed,
        batteryProfile,
        batterySettings,
        busSettings,
        view.kind === "loaded" ? (view.state.configuration_generation ?? 0) : 0,
      );
      setView({ kind: "loaded", state });
      setSelectedDevice(null);
      setMessage("Setup saved for review. Open Settings history to approve and apply it.");
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "The action could not finish.");
    } finally {
      endAction();
    }
  }

  if (view.kind === "loading") {
    return (
      <section className="lab" aria-labelledby="simulator-title">
        <PageTitle />
        <DataTableSkeleton columnCount={headers.length} rowCount={7} showHeader={false} />
        <p role="status" className="screen-message">
          Loading devices.
        </p>
      </section>
    );
  }

  if (view.kind === "error") {
    return (
      <section className="lab" aria-labelledby="simulator-title">
        <PageTitle />
        <InlineNotification
          kind="error"
          title="Simulator offline"
          subtitle={view.message}
          hideCloseButton
        />
        <Button onClick={load}>Retry</Button>
      </section>
    );
  }

  const state = view.state;
  const device = state.devices.find((item) => item.device_id === selectedDevice);
  return (
    <section className="lab" aria-labelledby="simulator-title">
      <PageTitle />
      {offline && (
        <InlineNotification
          kind="error"
          title="Simulator offline"
          subtitle={`${offlineMessage} The last received state is shown. Reconnecting.`}
          hideCloseButton
        />
      )}
      {state.restored_run !== null && state.lifecycle === "stopped" && (
        <InlineNotification
          kind="info"
          title={`Saved run restored at step ${String(state.restored_run.sequence)}. Paused.`}
          subtitle={`Simulation time: ${state.restored_run.simulation_at}. Saved: ${new Date(state.restored_run.saved_at).toLocaleString()}. Select Continue when ready.`}
          hideCloseButton
        />
      )}
      {state.failure !== null && (
        <InlineNotification
          kind="error"
          title="Simulation paused after a problem"
          subtitle={`Step ${String(state.failure.sequence)} could not finish. The last complete results are still shown. Review your test values, then try again.`}
          hideCloseButton
        />
      )}
      {busy === "step" && <p role="status">Calculating the next step.</p>}
      {device === undefined ? (
        <>
          <nav className="lab-navigation" aria-label="Simulator views">
            {(["overview", "plant", "devices", "faults", "activity"] as const).map((item) => (
              <Button
                key={item}
                kind={labView === item ? "primary" : "ghost"}
                size="sm"
                onClick={() => {
                  setLabView(item);
                }}
              >
                {viewLabels[item]}
              </Button>
            ))}
          </nav>
          {labView === "overview" && <Overview state={state} load={load} busy={busy} run={run} />}
          {labView === "plant" && (
            <PlantLab
              state={state}
              busy={busy !== null}
              run={run}
              applyProfile={applyProfile}
              download={downloadReport}
            />
          )}
          {labView === "devices" && (
            <Devices
              state={state}
              open={(deviceId) => {
                setSelectedDevice(deviceId);
              }}
            />
          )}
          {labView === "faults" && (
            <FaultTesting
              state={state}
              busy={busy !== null}
              start={(request) => void startFault(request)}
              clear={(testId) => void clearFault(testId)}
              step={() => void run("step")}
              download={() => void downloadReport()}
            />
          )}
          {labView === "activity" && <Activity state={state} />}
        </>
      ) : (
        <DeviceDetail
          key={device.device_id}
          device={device}
          gateway={state.gateway}
          busy={busy}
          back={() => {
            setSelectedDevice(null);
          }}
          save={(inputs) => void save(device.device_id, inputs)}
          step={() => void run("step")}
        />
      )}
      <div className="screen-message" role="status" aria-live="polite">
        {message}
      </div>
      {actionError && (
        <InlineNotification
          kind="error"
          title="Action failed"
          subtitle={actionError}
          hideCloseButton
        />
      )}
    </section>
  );
}

function Overview({
  state,
  load,
  busy,
  run,
}: {
  state: SimulationState;
  load: () => void;
  busy: BusyState;
  run: (action: SimulationAction) => Promise<void>;
}) {
  return (
    <>
      <div className="summary-grid">
        <Tile>
          <span>Status</span>
          <strong>{title(state.lifecycle)}</strong>
        </Tile>
        <Tile>
          <span>Steps</span>
          <strong>{state.sequence}</strong>
        </Tile>
        <Tile>
          <span>Devices</span>
          <strong>{state.devices.length}</strong>
        </Tile>
        <Tile>
          <span>Stabiliti available</span>
          <strong>
            {state.stabiliti_fleet.available} of {state.stabiliti_fleet.total}
          </strong>
          <span>
            {state.stabiliti_fleet.ready} ready · {state.stabiliti_fleet.derated} derated ·{" "}
            {state.stabiliti_fleet.faulted} faulted
          </span>
          <span>{state.stabiliti_fleet.available_power} kW available</span>
        </Tile>
      </div>
      <Tile className="simulator-connection">
        <div className="connection-heading">
          <div>
            <h2>Modbus TCP connection</h2>
            <p>{gatewayLabel(state.gateway)}</p>
          </div>
          <Button kind="secondary" size="sm" onClick={load}>
            Refresh connection
          </Button>
        </div>
        <dl className="connection-summary">
          <div>
            <dt>Address</dt>
            <dd>{state.gateway.host}</dd>
          </div>
          <div>
            <dt>Port</dt>
            <dd>{state.gateway.port}</dd>
          </div>
          <div>
            <dt>Connected clients</dt>
            <dd>{clientLabel(state.gateway.clients)}</dd>
          </div>
          <div>
            <dt>Last request</dt>
            <dd>{state.gateway.last_request ?? "None yet"}</dd>
          </div>
        </dl>
      </Tile>
      <SimulationControls state={state} busy={busy !== null} run={run} />
    </>
  );
}

function Devices({ state, open }: { state: SimulationState; open: (id: string) => void }) {
  if (state.devices.length === 0) {
    return (
      <InlineNotification
        kind="warning"
        title="No devices found"
        subtitle="The simulator returned an empty device list."
        hideCloseButton
      />
    );
  }
  return (
    <TableContainer title="Devices" description="Seven logical devices sharing one simulation.">
      <Table aria-label="Devices">
        <TableHead>
          <TableRow>
            {headers.map((header) => (
              <TableHeader key={header}>{header}</TableHeader>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {state.devices.map((device) => (
            <TableRow key={device.device_id}>
              <TableCell>
                <strong>{device.display_name}</strong>
                <span className="device-id">{deviceReference(device)}</span>
                <span className="table-detail">{roleLabel(device)}</span>
              </TableCell>
              <TableCell>{title(device.health)}</TableCell>
              <TableCell>{device.status_text}</TableCell>
              <TableCell>{connectionLabel(device, state.gateway)}</TableCell>
              <TableCell>
                <Button
                  kind="ghost"
                  size="sm"
                  onClick={() => {
                    open(device.device_id);
                  }}
                >
                  Open
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

function FaultTesting({
  state,
  busy,
  start,
  clear,
  step,
  download,
}: {
  state: SimulationState;
  busy: boolean;
  start: (request: StartSimulationFault) => void;
  clear: (testId: string) => void;
  step: () => void;
  download: () => void;
}) {
  const [deviceId, setDeviceId] = useState(state.devices[0]?.device_id ?? "");
  const [faultType, setFaultType] = useState("");
  const [pointId, setPointId] = useState("");
  const [testValue, setTestValue] = useState("");
  const [delaySteps, setDelaySteps] = useState(0);
  const [duration, setDuration] = useState("manual");
  const device = state.devices.find((item) => item.device_id === deviceId);
  const rules = state.fault_rules.filter((rule) => rule.device_ids.includes(deviceId));
  const rule = rules.find((item) => item.fault_type === faultType);
  const pointOptions = faultPointOptions(device, rule);
  const pointReady = rule?.needs_point !== true || pointId !== "";
  const valueReady = rule?.needs_value !== true || testValue !== "";
  const canStart = device !== undefined && rule !== undefined && pointReady && valueReady;

  function submit(event: SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (!canStart) return;
    start({
      device_id: deviceId,
      fault_type: rule.fault_type,
      point_id: rule.needs_point ? pointId : null,
      test_value: rule.needs_value ? Number(testValue) : null,
      delay_steps: delaySteps,
      duration_steps: rule.latched || duration === "manual" ? null : Number(duration),
    });
  }

  return (
    <section className="fault-testing" aria-labelledby="fault-testing-title">
      <div className="fault-heading">
        <div>
          <h2 id="fault-testing-title">Fault testing</h2>
          <p>Test device problems and confirm safe recovery.</p>
        </div>
        <ButtonSet>
          <Button kind="secondary" disabled={busy} onClick={step}>
            Next step
          </Button>
          <Button kind="tertiary" disabled={busy} onClick={download}>
            Download report
          </Button>
        </ButtonSet>
      </div>
      <Tile className="fault-form">
        <h3>Set up a fault test</h3>
        <form onSubmit={submit}>
          <div className="form-grid">
            <label className="test-field">
              <span>Device</span>
              <select
                value={deviceId}
                onChange={(event) => {
                  setDeviceId(event.target.value);
                  setFaultType("");
                  setPointId("");
                  setTestValue("");
                }}
              >
                {state.devices.map((item) => (
                  <option key={item.device_id} value={item.device_id}>
                    {item.display_name}
                  </option>
                ))}
              </select>
            </label>
            <label className="test-field">
              <span>Problem to test</span>
              <select
                value={faultType}
                onChange={(event) => {
                  setFaultType(event.target.value);
                  setPointId("");
                  setTestValue("");
                  setDuration("manual");
                }}
              >
                <option value="">Choose a problem</option>
                {rules.map((item) => (
                  <option key={item.fault_type} value={item.fault_type}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            {rule?.needs_point === true && (
              <label className="test-field">
                <span>Value to test</span>
                <select
                  value={pointId}
                  onChange={(event) => {
                    setPointId(event.target.value);
                  }}
                >
                  <option value="">Choose a value</option>
                  {pointOptions.map((item) => (
                    <option key={item.logical_id} value={item.logical_id}>
                      {valueLabel(item.logical_id)}
                    </option>
                  ))}
                </select>
                {pointOptions.length === 0 && <small>Run one normal step to load values.</small>}
              </label>
            )}
            {rule?.needs_value === true && (
              <label className="test-field">
                <span>Test value</span>
                <input
                  type="number"
                  step="any"
                  value={testValue}
                  onChange={(event) => {
                    setTestValue(event.target.value);
                  }}
                />
              </label>
            )}
            <label className="test-field">
              <span>Starts</span>
              <select
                value={delaySteps}
                onChange={(event) => {
                  setDelaySteps(Number(event.target.value));
                }}
              >
                <option value={0}>Next step</option>
                <option value={1}>In 2 steps</option>
                <option value={2}>In 3 steps</option>
                <option value={4}>In 5 steps</option>
              </select>
            </label>
            <label className="test-field">
              <span>Duration</span>
              {rule?.latched === true ? (
                <select value="manual" disabled>
                  <option value="manual">Until manually cleared</option>
                </select>
              ) : (
                <select
                  value={duration}
                  onChange={(event) => {
                    setDuration(event.target.value);
                  }}
                >
                  <option value="manual">Until manually cleared</option>
                  <option value="1">1 step</option>
                  <option value="2">2 steps</option>
                  <option value="5">5 steps</option>
                </select>
              )}
            </label>
          </div>
          {rule !== undefined && (
            <Tile className="expected-result">
              <span>Expected result</span>
              <strong>{rule.expected_result}</strong>
              <small>{rule.clear_method}</small>
            </Tile>
          )}
          <Button type="submit" disabled={busy || !canStart}>
            Start test
          </Button>
        </form>
      </Tile>
      <CurrentFaultTests state={state} busy={busy} clear={clear} />
    </section>
  );
}

function faultPointOptions(
  device: SimulationDevice | undefined,
  rule: SimulationFaultRule | undefined,
): SimulationValue[] {
  if (device === undefined || !rule?.needs_point) return [];
  if (rule.fault_type !== "out_of_range") return device.values;
  return device.values.filter(
    (item) => typeof item.value === "number" && Number.isFinite(item.value),
  );
}

function CurrentFaultTests({
  state,
  busy,
  clear,
}: {
  state: SimulationState;
  busy: boolean;
  clear: (testId: string) => void;
}) {
  if (state.fault_tests.length === 0) {
    return (
      <InlineNotification
        kind="info"
        title="No fault tests yet"
        subtitle="Choose a device and problem above to begin."
        hideCloseButton
      />
    );
  }
  return (
    <Tile className="current-tests">
      <h3>Current tests</h3>
      <div className="fault-test-list">
        {state.fault_tests.map((test) => {
          const device = state.devices.find((item) => item.device_id === test.device_id);
          const clearable = test.state === "scheduled" || test.state === "active";
          return (
            <article key={test.test_id} className="fault-test-card">
              <div>
                <span>Test ID</span>
                <strong>{test.test_id}</strong>
              </div>
              <div>
                <span>Device</span>
                <strong>{device?.display_name ?? test.device_id}</strong>
              </div>
              <div>
                <span>Problem</span>
                <strong>{test.label}</strong>
              </div>
              <div>
                <span>Status</span>
                <strong>{faultStateLabel(test.state)}</strong>
              </div>
              {test.point_id !== null && (
                <div>
                  <span>Value</span>
                  <strong>{valueLabel(test.point_id)}</strong>
                </div>
              )}
              {clearable && (
                <Button
                  kind="danger--tertiary"
                  size="sm"
                  disabled={busy}
                  onClick={() => {
                    clear(test.test_id);
                  }}
                >
                  Clear test
                </Button>
              )}
            </article>
          );
        })}
      </div>
    </Tile>
  );
}

function faultStateLabel(state: string): string {
  if (state === "scheduled") return "Scheduled";
  if (state === "active") return "Active";
  if (state === "cleared") return "Cleared";
  return "Complete";
}

function Activity({ state }: { state: SimulationState }) {
  if (state.activity.length === 0) {
    return (
      <InlineNotification
        kind="info"
        title="No activity yet"
        subtitle="Run a simulation step to create activity."
        hideCloseButton
      />
    );
  }
  return (
    <Tile className="activity-list">
      <h2>Activity</h2>
      <ol>
        {state.activity.map((item, index) => (
          <li key={`${String(item.sequence)}:${item.device_id}:${String(index)}`}>
            <strong>
              Step {item.sequence} · {item.device_id}
              {item.test_id === null ? "" : ` · ${item.test_id}`}
            </strong>
            <span>{item.message}</span>
            {item.test_started_at !== null && (
              <span>Test started {formatDateTime(item.test_started_at)}</span>
            )}
          </li>
        ))}
      </ol>
    </Tile>
  );
}

function DeviceDetail({
  device,
  gateway,
  busy,
  back,
  save,
  step,
}: {
  device: SimulationDevice;
  gateway: SimulationGateway;
  busy: BusyState;
  back: () => void;
  save: (inputs: SimulationInputs) => void;
  step: () => void;
}) {
  const route = gateway.routes.find((item) => item.device_id === device.device_id);
  return (
    <>
      <div className="device-heading">
        <div>
          <p>{deviceReference(device)}</p>
          <h2>{device.display_name}</h2>
          <p>{roleLabel(device)}</p>
        </div>
        <Button kind="secondary" size="sm" onClick={back}>
          Back
        </Button>
      </div>
      <div className="device-status-grid">
        <Tile>
          <span>Simulation</span>
          <strong>{device.status_text}</strong>
          {device.test_id !== null && <span>Test ID: {device.test_id}</span>}
        </Tile>
        <Tile>
          <span>Testing method</span>
          <strong>{device.testing_method}</strong>
        </Tile>
        <Tile>
          <span>Test connection</span>
          <strong>{device.direct_connection}</strong>
        </Tile>
      </div>
      {device.test_notice !== null && (
        <InlineNotification
          kind={
            device.device_id.startsWith("st-") || device.device_id === "dc600" ? "info" : "warning"
          }
          title={
            device.device_id.startsWith("st-") || device.device_id === "dc600"
              ? "How this test works"
              : "Test behaviour"
          }
          subtitle={device.test_notice}
          hideCloseButton
        />
      )}
      <p role="status">
        {device.inputs_pending
          ? "Saved test inputs are waiting for the next step. Current readings reflect previously applied inputs."
          : "The accepted test inputs have been applied."}
      </p>
      <div className="device-detail-grid">
        <CurrentValues device={device} />
        <InputForm inputs={device.inputs} disabled={busy !== null} save={save} step={step} />
      </div>
      {route !== undefined && <RegisterInstructions route={route} />}
    </>
  );
}

function CurrentValues({ device }: { device: SimulationDevice }) {
  return (
    <Tile className="current-values">
      <h3>Current values</h3>
      {device.values.length === 0 ? (
        <p>Value unavailable. Run the next step.</p>
      ) : (
        <dl>
          {device.values.map((item) => (
            <div key={item.logical_id}>
              <dt>{valueLabel(item.logical_id)}</dt>
              <dd>
                {formatValue(item)}
                <span>{title(item.quality)}</span>
                {item.test_id !== null && <span>Test ID: {item.test_id}</span>}
              </dd>
            </div>
          ))}
        </dl>
      )}
      {device.device_id === "dc600" && <Dc600Availability device={device} />}
      {device.device_id === "ba1-1" && (
        <dl>
          <div>
            <dt>State of health</dt>
            <dd>Not available</dd>
          </div>
        </dl>
      )}
      {device.faults.length > 0 && (
        <InlineNotification
          kind="error"
          title="Tracked fault test active"
          subtitle={
            device.test_id === null ? "The device reports a fault." : `Test ID: ${device.test_id}`
          }
          hideCloseButton
        />
      )}
    </Tile>
  );
}

const prechargeStateLabels: Readonly<Record<number, string>> = {
  0: "Disconnected",
  1: "Starting grid connection",
  2: "Grid connecting",
  3: "Grid connection successful",
  4: "Grid connection failed",
};

const fireCommunicationStateLabels: Readonly<Record<number, string>> = {
  0: "Reserved",
  1: "Normal",
  2: "No communication",
};

const stateLabels: Readonly<Record<string, Readonly<Record<number, string>>>> = {
  "bess.cluster.precharge.state": prechargeStateLabels,
  "bess.fire.communication.state": fireCommunicationStateLabels,
};

function Dc600Availability({ device }: { device: SimulationDevice }) {
  const values = new Map(device.values.map((item) => [item.logical_id, item]));
  return (
    <dl>
      {!values.has("dc600.actual.power") && (
        <div>
          <dt>Actual power</dt>
          <dd>Not available</dd>
        </div>
      )}
    </dl>
  );
}

function formatValue({ logical_id: logicalId, value, unit, quality }: SimulationValue): string {
  if (quality === "comm_error") return "Communication lost";
  if (quality === "invalid") return "Value unavailable";
  const formatted = formatGoodValue(logicalId, value, unit);
  if (quality === "stale") return `${formatted} · Stale reading`;
  if (quality === "out_of_range") return `${formatted} · Outside allowed range`;
  if (quality !== "good") return "Value unavailable";
  return formatted;
}

function formatGoodValue(logicalId: string, value: SimulationValue["value"], unit: string): string {
  if (unit === "state_code" && typeof value === "number") {
    return stateLabels[logicalId]?.[value] ?? `Unknown state (code ${String(value)})`;
  }
  if (typeof value === "boolean") return formatBoolean(logicalId, value);
  if (typeof value === "number" && unit === "ratio") return `${(value * 100).toFixed(1)} %`;
  if (unit === "timestamp") {
    const parsed = new Date(String(value));
    return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
  }
  const displayUnit = unit === "degC" ? "°C" : unit;
  return `${String(value)}${displayUnit === "boolean" || displayUnit === "text" ? "" : ` ${displayUnit}`}`;
}

function formatDateTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function formatBoolean(logicalId: string, value: boolean): string {
  if (logicalId.endsWith(".communication.ok")) return value ? "Connected" : "Disconnected";
  if (logicalId.endsWith(".fault.active")) return value ? "Active" : "None";
  if (logicalId.endsWith(".handshake.live")) return value ? "Live" : "Not live";
  if (logicalId.endsWith(".session.active")) return value ? "Active" : "Inactive";
  if (logicalId.endsWith(".request.limited")) return value ? "Yes" : "No";
  if (logicalId.endsWith(".available")) return value ? "Available" : "Unavailable";
  if (logicalId.endsWith(".derated")) return value ? "Yes" : "No";
  if (logicalId.endsWith(".enabled")) return value ? "Enabled" : "Disabled";
  if (logicalId.endsWith(".ready")) return value ? "Ready" : "Not ready";
  return value ? "On" : "Off";
}

function RegisterInstructions({ route }: { route: SimulationGateway["routes"][number] }) {
  return (
    <Tile className="register-instructions">
      <h3>Modbus read instructions</h3>
      <p>Device ID {route.modbus_id}. These generated points are read-only.</p>
      {route.registers.length === 0 ? (
        <p>Start the configured gateway to load register details.</p>
      ) : (
        <ul>
          {route.registers.map((item) => (
            <li key={`${item.logical_id}:${String(item.address)}`}>
              <code>{item.display}</code> · function {item.function} · count {item.count} ·{" "}
              {item.logical_id}
            </li>
          ))}
        </ul>
      )}
    </Tile>
  );
}

function InputForm({
  inputs,
  disabled,
  save,
  step,
}: {
  inputs: SimulationInputs;
  disabled: boolean;
  save: (value: SimulationInputs) => void;
  step: () => void;
}) {
  if (inputs.kind === "battery")
    return <BatteryForm initial={inputs} disabled={disabled} save={save} step={step} />;
  if (inputs.kind === "converter")
    return <ConverterForm initial={inputs} disabled={disabled} save={save} step={step} />;
  if (inputs.kind === "stabiliti")
    return <StabilitiForm initial={inputs} disabled={disabled} save={save} step={step} />;
  return <Dc600Form initial={inputs} disabled={disabled} save={save} step={step} />;
}

function FormShell({
  children,
  disabled,
  submit,
  step,
}: {
  children: ReactNode;
  disabled: boolean;
  submit: (event: SyntheticEvent<HTMLFormElement>) => void;
  step: () => void;
}) {
  return (
    <Tile className="input-form">
      <h3>Set test values</h3>
      <form onSubmit={submit}>
        <div className="form-grid">{children}</div>
        <ButtonSet>
          <Button type="submit" disabled={disabled}>
            Save for review
          </Button>
          <Button type="button" kind="secondary" disabled={disabled} onClick={step}>
            Next step
          </Button>
        </ButtonSet>
      </form>
    </Tile>
  );
}

function NumberField({
  label,
  value,
  set,
  nullable = false,
}: {
  label: string;
  value: number | null;
  set: (value: number | null) => void;
  nullable?: boolean;
}) {
  return (
    <label className="test-field">
      <span>{label}</span>
      <input
        type="number"
        step="any"
        value={value ?? ""}
        onChange={(event) => {
          set(event.target.value === "" && nullable ? null : Number(event.target.value));
        }}
      />
    </label>
  );
}

function BoolField({
  label,
  checked,
  set,
}: {
  label: string;
  checked: boolean;
  set: (value: boolean) => void;
}) {
  return (
    <label className="test-checkbox">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => {
          set(event.target.checked);
        }}
      />
      <span>{label}</span>
    </label>
  );
}

function StateField({
  label,
  value,
  options,
  set,
}: {
  label: string;
  value: number;
  options: Readonly<Record<number, string>>;
  set: (value: number) => void;
}) {
  return (
    <label className="test-field">
      <span>{label}</span>
      <select
        value={value}
        onChange={(event) => {
          set(Number(event.target.value));
        }}
      >
        {Object.entries(options).map(([code, state]) => (
          <option key={code} value={code}>
            {state}
          </option>
        ))}
      </select>
    </label>
  );
}

function BatteryForm({
  initial,
  disabled,
  save,
  step,
}: {
  initial: BatteryInputs;
  disabled: boolean;
  save: (value: SimulationInputs) => void;
  step: () => void;
}) {
  const [value, setValue] = useState(initial);
  const number = (key: keyof BatteryInputs) => (next: number | null) => {
    setValue({ ...value, [key]: next ?? 0 });
  };
  const bool = (key: keyof BatteryInputs) => (next: boolean) => {
    setValue({ ...value, [key]: next });
  };
  return (
    <FormShell
      disabled={disabled}
      step={step}
      submit={(event) => {
        event.preventDefault();
        save(value);
      }}
    >
      <NumberField
        label="Reported battery level (0–1, test input)"
        value={value.cluster_soc}
        set={number("cluster_soc")}
      />
      <NumberField label="Voltage (V)" value={value.voltage} set={number("voltage")} />
      <NumberField label="Current (A)" value={value.current} set={number("current")} />
      <NumberField
        label="Maximum temperature (°C)"
        value={value.maximum_temperature}
        set={number("maximum_temperature")}
      />
      <NumberField
        label="Charge power limit (kW)"
        value={value.charge_power_limit}
        set={number("charge_power_limit")}
      />
      <NumberField
        label="Discharge power limit (kW)"
        value={value.discharge_power_limit}
        set={number("discharge_power_limit")}
      />
      <StateField
        label="Precharge state"
        value={value.precharge_state}
        options={prechargeStateLabels}
        set={(next) => {
          setValue({ ...value, precharge_state: next });
        }}
      />
      <StateField
        label="Fire communication state"
        value={value.fire_communication_state}
        options={fireCommunicationStateLabels}
        set={(next) => {
          setValue({ ...value, fire_communication_state: next });
        }}
      />
      <BoolField
        label="Main contactor closed"
        checked={value.main_contactor}
        set={bool("main_contactor")}
      />
      <BoolField
        label="Precharge contactor closed"
        checked={value.precharge_contactor}
        set={bool("precharge_contactor")}
      />
    </FormShell>
  );
}

function ConverterForm({
  initial,
  disabled,
  save,
  step,
}: {
  initial: ConverterInputs;
  disabled: boolean;
  save: (value: SimulationInputs) => void;
  step: () => void;
}) {
  const [value, setValue] = useState(initial);
  const number = (key: keyof ConverterInputs) => (next: number | null) => {
    setValue({ ...value, [key]: next });
  };
  const bool = (key: keyof ConverterInputs) => (next: boolean) => {
    setValue({ ...value, [key]: next });
  };
  return (
    <FormShell
      disabled={disabled}
      step={step}
      submit={(event) => {
        event.preventDefault();
        save(value);
      }}
    >
      <NumberField
        label="Requested power (kW)"
        value={value.requested_power}
        set={number("requested_power")}
      />
      <NumberField
        label="Available power (kW)"
        value={value.available_power}
        set={number("available_power")}
      />
      <NumberField
        label="Charge power limit (kW)"
        value={value.charge_power_limit}
        set={number("charge_power_limit")}
      />
      <NumberField
        label="Discharge power limit (kW)"
        value={value.discharge_power_limit}
        set={number("discharge_power_limit")}
      />
      <NumberField label="DC voltage (V)" value={value.dc_voltage} set={number("dc_voltage")} />
      <NumberField label="DC current (A)" value={value.dc_current} set={number("dc_current")} />
      <NumberField label="Temperature (°C)" value={value.temperature} set={number("temperature")} />
      <NumberField
        label="Power change per step (kW)"
        value={value.test_ramp_per_step}
        nullable
        set={number("test_ramp_per_step")}
      />
      <BoolField label="Enabled" checked={value.enabled} set={bool("enabled")} />
      <BoolField label="PV available" checked={value.pv_available} set={bool("pv_available")} />
      <BoolField
        label="Maximum power tracking active"
        checked={value.mppt_applying}
        set={bool("mppt_applying")}
      />
    </FormShell>
  );
}

function StabilitiForm({
  initial,
  disabled,
  save,
  step,
}: {
  initial: StabilitiInputs;
  disabled: boolean;
  save: (value: SimulationInputs) => void;
  step: () => void;
}) {
  const [value, setValue] = useState(initial);
  return (
    <FormShell
      disabled={disabled}
      step={step}
      submit={(event) => {
        event.preventDefault();
        save(value);
      }}
    >
      <p>
        Power comes from the DC bus. Calculated AC output uses the power actually supplied, assuming
        no conversion loss. Reported AC power is a separate test reading.
      </p>
      <NumberField
        label="Power needed from DC bus (kW)"
        value={value.bus_load}
        nullable
        set={(next) => {
          setValue({ ...value, bus_load: next });
        }}
      />
      <NumberField
        label="Available power (kW)"
        value={value.available_power}
        set={(next) => {
          setValue({ ...value, available_power: next ?? 0 });
        }}
      />
      <NumberField
        label="Reported AC power (kW)"
        value={value.actual_power}
        set={(next) => {
          setValue({ ...value, actual_power: next ?? 0 });
        }}
      />
      <NumberField
        label="Power limit (kW)"
        value={value.power_limit}
        set={(next) => {
          setValue({ ...value, power_limit: next ?? 0 });
        }}
      />
      <NumberField
        label="Maximum temperature (°C)"
        value={value.maximum_temperature}
        set={(next) => {
          setValue({ ...value, maximum_temperature: next ?? 0 });
        }}
      />
      <BoolField
        label="Enabled"
        checked={value.enabled}
        set={(next) => {
          setValue({ ...value, enabled: next });
        }}
      />
    </FormShell>
  );
}

function Dc600Form({
  initial,
  disabled,
  save,
  step,
}: {
  initial: Dc600Inputs;
  disabled: boolean;
  save: (value: SimulationInputs) => void;
  step: () => void;
}) {
  const [value, setValue] = useState(initial);
  return (
    <FormShell
      disabled={disabled}
      step={step}
      submit={(event) => {
        event.preventDefault();
        save(value);
      }}
    >
      <NumberField
        label="Demand power (kW)"
        value={value.demand_power}
        nullable
        set={(next) => {
          setValue({ ...value, demand_power: next });
        }}
      />
      <NumberField
        label="Available power (kW)"
        value={value.available_power}
        nullable
        set={(next) => {
          setValue({ ...value, available_power: next });
        }}
      />
      <NumberField
        label="Actual power (kW)"
        value={value.actual_power}
        nullable
        set={(next) => {
          setValue({ ...value, actual_power: next });
        }}
      />
      <BoolField
        label="Charging session active"
        checked={value.session_active}
        set={(next) => {
          setValue({ ...value, session_active: next });
        }}
      />
    </FormShell>
  );
}
