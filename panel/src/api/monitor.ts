import { isRecord, isOneOf, parseCurrentState, type CurrentState } from "./currentState";

export interface Connection {
  device_id: "ba1-1";
  mode: "sim" | "live";
  host: string;
  port: number;
  unit_id: number;
  poll_seconds: number;
  timeout_seconds: number;
  stale_seconds: number;
}
export interface SavedConfig {
  revision: number;
  active: string | null;
  connections: Record<string, Connection>;
}
export interface MonitorState {
  revision: number;
  mode: "sim" | "live" | null;
  connection:
    | "not_configured"
    | "connecting"
    | "connected"
    | "disconnected"
    | "stale"
    | "invalid"
    | "storage_error";
  message: string;
  state: CurrentState;
  stale_seconds: number;
}
export function parseMonitor(value: unknown): MonitorState {
  if (
    !isRecord(value) ||
    typeof value.revision !== "number" ||
    !Number.isInteger(value.revision) ||
    (value.mode !== null && !isOneOf(value.mode, ["sim", "live"])) ||
    !isOneOf(value.connection, [
      "not_configured",
      "connecting",
      "connected",
      "disconnected",
      "stale",
      "invalid",
      "storage_error",
    ]) ||
    typeof value.message !== "string" ||
    typeof value.stale_seconds !== "number" ||
    !Number.isFinite(value.stale_seconds) ||
    value.stale_seconds <= 0
  )
    throw new Error("Invalid monitoring response");
  const state = parseCurrentState(value.state);
  if (
    !Number.isFinite(Date.parse(state.observed_at)) ||
    (state.measurement !== null &&
      (state.measurement.point_id !== "bess.cluster.soc" ||
        state.measurement.unit !== "ratio" ||
        state.measurement.value < 0 ||
        state.measurement.value > 1 ||
        !Number.isFinite(Date.parse(state.measurement.receive_timestamp))))
  )
    throw new Error("Invalid battery reading");
  return {
    revision: value.revision,
    mode: value.mode,
    connection: value.connection,
    message: value.message,
    state,
    stale_seconds: value.stale_seconds,
  };
}
export function streamUrl(apiBaseUrl: string): string {
  const url = new URL(`${apiBaseUrl}/v1/monitor/stream`, window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}
