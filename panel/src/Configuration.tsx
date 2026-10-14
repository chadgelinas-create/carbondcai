import { useCallback, useEffect, useState } from "react";
import {
  Button,
  InlineLoading,
  InlineNotification,
  Modal,
  Select,
  SelectItem,
  Tag,
  Tile,
} from "@carbon/react";
import type { ApiClient } from "./api/client";
import { batteryNames, profileNames } from "./api/simulation";

interface Revision {
  id: number;
  name: string;
  reason: string;
  created_by: string;
  created_at: string;
  approved_by: string | null;
  approved_at: string | null;
}
interface SettingsState {
  head: { revision_id: number | null; generation: number; pending_action: string | null };
  installed: { revision_id: number } | null;
  message: string;
  revisions: Revision[];
  next_before: number | null;
}
interface Change {
  field: string;
  before: unknown;
  after: unknown;
}
interface Comparison {
  generation: number;
  changes: Change[];
}
interface Activity {
  id: string;
  actor: string;
  kind: string;
  outcome: string;
  message: string;
  created_at: string;
}

async function read<T>(response: Response): Promise<T> {
  const value: unknown = await response.json();
  if (!response.ok) {
    const detail =
      value !== null && typeof value === "object" && "detail" in value ? value.detail : null;
    throw new Error(
      typeof detail === "string" ? detail : "Settings could not be loaded. Try again.",
    );
  }
  return value as T;
}

const fieldNames: Readonly<Record<string, string>> = {
  active: "Selected connection",
  device_id: "Device",
  mode: "Data source",
  host: "Device address",
  port: "Connection port",
  unit_id: "Modbus device number",
  poll_seconds: "Read interval (seconds)",
  timeout_seconds: "Connection timeout (seconds)",
  stale_seconds: "Reading expiry (seconds)",
  profile_id: "Simulation scenario",
  seed: "Repeatable run number",
  step_seconds: "Step duration (seconds)",
  capacity_kwh: "Battery capacity (kWh)",
  initial_energy_kwh: "Starting stored energy (kWh)",
  charge_efficiency: "Charging efficiency (ratio)",
  discharge_efficiency: "Discharging efficiency (ratio)",
  charge_limit_kw: "Charging limit (kW)",
  discharge_limit_kw: "Discharging limit (kW)",
  nominal_voltage_v: "Nominal voltage (V)",
  max_voltage_v: "Maximum voltage (V)",
  capacitance_f: "Capacitance (F)",
  loss_fraction: "Power loss (ratio)",
  loss_ratio: "Power loss (ratio)",
  tolerance_kw: "Balance tolerance (kW)",
  soc: "Reported battery charge (ratio)",
  cluster_soc: "Reported battery charge (ratio)",
  maximum_temperature: "Highest temperature (°C)",
  charge_power_limit: "Charging limit (kW)",
  discharge_power_limit: "Discharging limit (kW)",
  main_contactor: "Main contactor closed",
  precharge_contactor: "Precharge contactor closed",
  precharge_state: "Precharge status",
  fire_communication_state: "Fire-system communication",
  requested_power: "Requested power (kW)",
  dc_voltage: "DC voltage (V)",
  dc_current: "DC current (A)",
  pv_available: "Solar power available",
  mppt_applying: "Solar tracking active",
  test_ramp_per_step: "Power change per step (kW)",
  test_derate_limit: "Reduced power limit (kW)",
  bus_load: "Power needed from DC bus (kW)",
  derated: "Operating at reduced power",
  demand_fresh: "Charger request is current",
  available_fresh: "Available-power reading is current",
  session_active: "Charging session active",
  voltage: "Reported voltage (V)",
  current: "Reported current (A)",
  temperature: "Temperature (°C)",
  enabled: "Enabled",
  ready: "Ready",
  available: "Available",
  actual_power: "Actual power (kW)",
  available_power: "Available power (kW)",
  demand_power: "Requested power (kW)",
  power_limit: "Power limit (kW)",
  communication_ok: "Communication available",
  fault_active: "Test fault active",
};
const devices: Readonly<Record<string, string>> = {
  "ba1-1": "Battery",
  "cnvtr-1": "Battery converter",
  "cnvtr-2": "Solar converter",
  "st-4": "Stabiliti 01",
  "st-5": "Stabiliti 02",
  "st-6": "Stabiliti 03",
  dc600: "EV charger",
};
function settingName(path: string): string {
  const keys = path.split(".");
  const key = keys.at(-1) ?? "";
  const group = keys.includes("battery")
    ? "Battery"
    : keys.includes("bus")
      ? "DC bus"
      : keys.includes("inputs")
        ? devices[keys[1] ?? ""]
        : undefined;
  const label =
    (key === "profile_id" && group === "Battery" ? "Battery setup" : fieldNames[key]) ??
    key.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
  return group ? `${group} · ${label}` : label;
}
function display(value: unknown): string {
  if (value === null || value === undefined) return "Not set";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number")
    return new Intl.NumberFormat(undefined, {
      maximumFractionDigits: 6,
      useGrouping: false,
    }).format(value);
  if (typeof value === "string") {
    return (
      profileNames[value] ??
      batteryNames[value] ??
      devices[value] ??
      (
        {
          sim: "Simulation",
          live: "Live equipment",
          "ba1-1:sim": "Simulated battery",
          "ba1-1:live": "Live battery",
        } as Record<string, string>
      )[value] ??
      value.replaceAll("_", " ")
    );
  }
  if (Array.isArray(value)) return value.map(display).join(", ");
  return "Configured";
}
const actionNames: Readonly<Record<string, string>> = {
  request_config: "Connection settings request",
  request_apply: "Apply settings request",
  request_approve: "Approval request",
  request_drafts: "Connection proposal",
  request_profiles: "Simulation setup proposal",
  access: "Access check",
  save_draft: "Settings saved for review",
  approve: "Settings approved",
  apply: "Settings applied",
  restore: "Earlier settings restored",
  start: "Simulation started",
  pause: "Simulation paused",
  step: "Simulation advanced",
  snapshot: "Run saved",
  reset: "Run reset",
  new: "New run",
};
const outcomeNames: Readonly<Record<string, string>> = {
  accepted: "Completed",
  applied: "Applied",
  rejected: "Not accepted",
  failed: "Failed",
  pending: "In progress",
  interrupted: "Interrupted by restart",
  timed_out: "Timed out",
};

export function Configuration({
  client,
  admin,
  simulationEnabled,
}: {
  client: ApiClient;
  admin: boolean;
  simulationEnabled: boolean;
}) {
  const [scope, setScope] = useState("monitor");
  const [state, setState] = useState<SettingsState | null>(null);
  const [selected, setSelected] = useState<Revision | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [confirm, setConfirm] = useState(false);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      const [settings, events] = await Promise.all([
        client
          .get(`/v1/configuration/${scope}`, { signal: signal ?? null })
          .then(read<SettingsState>),
        client
          .get(`/v1/configuration/${scope}/activity`, { signal: signal ?? null })
          .then(read<Activity[]>),
      ]);
      return [settings, events] as const;
    },
    [client, scope],
  );
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal)
      .then(([settings, events]) => {
        if (!controller.signal.aborted) {
          setState(settings);
          setActivity(events);
        }
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setError(reason instanceof Error ? reason.message : "Could not load settings.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
    };
  }, [load]);

  async function refresh() {
    const [settings, events] = await load();
    setState(settings);
    setActivity(events);
  }

  async function review(revision: Revision) {
    setSelected(revision);
    setComparison(null);
    setError("");
    setBusy(true);
    try {
      setComparison(
        await read<Comparison>(
          await client.get(`/v1/configuration/revisions/${String(revision.id)}/compare`),
        ),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not compare settings.");
    } finally {
      setBusy(false);
    }
  }
  async function act(action: "approve" | "apply") {
    if (!selected || !comparison) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await read(
        await client.post(`/v1/configuration/revisions/${String(selected.id)}/${action}`, {
          headers: { "Content-Type": "application/json" },
          body:
            action === "apply"
              ? JSON.stringify({
                  expected_generation: comparison.generation,
                  restore: selected.id < (state?.head.revision_id ?? 0),
                })
              : null,
        }),
      );
      await refresh();
      setSelected(null);
      setComparison(null);
      setConfirm(false);
      setNotice(
        action === "approve"
          ? "Settings approved. Review them again when you are ready to apply."
          : "Settings applied in the application. Check the current run or battery connection for its operating status.",
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The change could not be confirmed.");
      setConfirm(false);
      setComparison(null);
      try {
        await refresh();
      } catch {
        /* Keep the original actionable error. */
      }
    } finally {
      setBusy(false);
    }
  }
  async function older() {
    if (!state?.next_before) return;
    setBusy(true);
    try {
      const next = await read<SettingsState>(
        await client.get(`/v1/configuration/${scope}?before=${String(state.next_before)}`),
      );
      setState({ ...next, revisions: [...state.revisions, ...next.revisions] });
    } catch {
      setError("Older settings could not be loaded. Try again.");
    } finally {
      setBusy(false);
    }
  }
  const visibleChanges =
    comparison?.changes.filter(
      (item) =>
        !/(compatibility|schema_version|initial_at|limitations|revision|model_version|capabilities|review_owner|reviewers|configuration\.pv|demand_updated_at|available_updated_at)/.test(
          item.field,
        ),
    ) ?? [];
  return (
    <section className="settings-history" aria-labelledby="settings-title">
      <div className="settings-heading">
        <div>
          <h1 id="settings-title">Settings history</h1>
          <p>Review proposed changes, approve settings and restore an earlier setup.</p>
        </div>
        <Button
          kind="tertiary"
          disabled={busy || loading}
          onClick={() => {
            setSelected(null);
            setComparison(null);
            setError("");
            void refresh().catch(() => {
              setError("Could not refresh settings. Try again.");
            });
          }}
        >
          Refresh
        </Button>
      </div>
      <Select
        id="settings-area"
        labelText="Settings area"
        value={scope}
        disabled={busy}
        onChange={(event) => {
          setLoading(true);
          setState(null);
          setSelected(null);
          setComparison(null);
          setError("");
          setNotice("");
          setScope(event.target.value);
        }}
      >
        <SelectItem value="monitor" text="Battery connection" />
        {simulationEnabled && <SelectItem value="simulation" text="Simulation setup" />}
      </Select>
      {error && <InlineNotification kind="error" title={error} hideCloseButton />}
      {notice && <InlineNotification kind="success" title={notice} hideCloseButton />}
      {loading && <InlineLoading description="Loading settings history…" />}
      {state && (
        <>
          <Tile className="settings-current">
            <h2>Current settings</h2>
            <Tag type={state.installed ? "green" : "gray"}>
              {state.installed
                ? `Applied version ${String(state.installed.revision_id)}`
                : state.head.pending_action
                  ? "Change in progress"
                  : "Check current setup"}
            </Tag>
            <p>{state.message}</p>
            {state.head.revision_id && !state.installed && (
              <p>Last committed settings: version {state.head.revision_id}.</p>
            )}
            <p>
              To propose changes, edit the connection in Battery monitoring or the setup in
              Simulator Lab and select Save for review.
            </p>
            {!admin && (
              <p>You can review history. An administrator approves and applies settings.</p>
            )}
          </Tile>
          <div className="settings-layout">
            <Tile>
              <h2>Saved versions</h2>
              {state.revisions.length === 0 ? (
                <p>No proposed changes yet. Your current settings remain in use.</p>
              ) : (
                <ul className="settings-version-list" tabIndex={0} aria-label="Saved versions">
                  {state.revisions.map((revision) => (
                    <li key={revision.id}>
                      <div>
                        <strong>{revision.name}</strong>
                        <p>
                          Version {revision.id} · {new Date(revision.created_at).toLocaleString()}
                        </p>
                        <p>Saved by {revision.created_by}</p>
                        <Tag type={revision.approved_by ? "green" : "blue"}>
                          {revision.approved_by ? "Approved" : "Awaiting review"}
                        </Tag>
                      </div>
                      <Button
                        kind={selected?.id === revision.id ? "primary" : "tertiary"}
                        size="sm"
                        disabled={busy}
                        onClick={() => void review(revision)}
                        aria-label={`Review version ${String(revision.id)}`}
                      >
                        Review
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
              {state.next_before && (
                <Button kind="ghost" disabled={busy} onClick={() => void older()}>
                  Load older versions
                </Button>
              )}
            </Tile>
            <Tile aria-live="polite" role="region" aria-label="Settings comparison">
              <h2>{selected ? selected.name : "Review a saved version"}</h2>
              {!selected && (
                <p>Select Review to compare a proposal with the last committed settings.</p>
              )}
              {selected && (
                <>
                  <p>{selected.reason}</p>
                  <p>
                    {selected.approved_by
                      ? `Approved by ${selected.approved_by}`
                      : "Awaiting administrator approval"}
                  </p>
                  {busy && !comparison && <InlineLoading description="Comparing settings…" />}
                  {comparison && (
                    <>
                      <div className="settings-table-wrap">
                        <table className="settings-diff">
                          <caption>Changes compared with the last committed settings</caption>
                          <thead>
                            <tr>
                              <th scope="col">Setting</th>
                              <th scope="col">Last applied</th>
                              <th scope="col">Proposed</th>
                            </tr>
                          </thead>
                          <tbody>
                            {visibleChanges.map((change) => (
                              <tr key={change.field}>
                                <th scope="row">{settingName(change.field)}</th>
                                <td>{display(change.before)}</td>
                                <td>{display(change.after)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      {visibleChanges.length === 0 && (
                        <p>These settings match the last applied setup.</p>
                      )}
                      {admin && (
                        <div className="settings-actions">
                          {!selected.approved_by ? (
                            <Button disabled={busy} onClick={() => void act("approve")}>
                              Approve settings
                            </Button>
                          ) : (
                            <Button
                              disabled={
                                busy ||
                                !!state.head.pending_action ||
                                state.installed?.revision_id === selected.id
                              }
                              onClick={() => {
                                setConfirm(true);
                              }}
                            >
                              {selected.id < (state.head.revision_id ?? 0)
                                ? "Restore these settings"
                                : "Apply settings"}
                            </Button>
                          )}
                        </div>
                      )}
                    </>
                  )}
                </>
              )}
            </Tile>
          </div>
          <Tile>
            <h2>Recent activity</h2>
            <p>The latest 50 actions, including unsuccessful attempts.</p>
            {activity.length === 0 ? (
              <p>No recorded actions yet.</p>
            ) : (
              <ul className="settings-activity" tabIndex={0} aria-label="Recent settings activity">
                {activity
                  .filter(
                    (item) => !item.kind.startsWith("request_") || item.outcome !== "accepted",
                  )
                  .map((item) => (
                    <li key={item.id}>
                      <div>
                        <strong>{actionNames[item.kind] ?? "Simulation action"}</strong>
                        <Tag
                          type={
                            item.outcome === "applied" || item.outcome === "accepted"
                              ? "green"
                              : "gray"
                          }
                        >
                          {outcomeNames[item.outcome] ?? "Recorded"}
                        </Tag>
                      </div>
                      <p>{item.message}</p>
                      <p>
                        {item.actor} · {new Date(item.created_at).toLocaleString()}
                      </p>
                    </li>
                  ))}
              </ul>
            )}
          </Tile>
        </>
      )}
      <Modal
        open={confirm}
        modalHeading="Apply these settings?"
        primaryButtonText={busy ? "Applying…" : "Apply settings"}
        secondaryButtonText="Cancel"
        primaryButtonDisabled={busy}
        onRequestClose={() => {
          if (!busy) setConfirm(false);
        }}
        onRequestSubmit={() => void act("apply")}
      >
        <p>
          {scope === "simulation"
            ? "Pause the simulation first. Applying starts a new paused run and discards unsaved progress. Your manually saved run is retained."
            : "The battery connection will restart with these settings. New readings appear only after the device responds."}
        </p>
        <p>
          Version {selected?.id}: {selected?.name}
        </p>
      </Modal>
    </section>
  );
}
