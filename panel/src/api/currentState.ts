import type { ApiClient } from "./client";

export type DataQuality = "good" | "stale" | "invalid" | "comm_error" | "out_of_range";
export type Freshness = "live" | "stale" | "unavailable";
export type ComponentStatus = "healthy" | "degraded" | "unavailable" | "stopped" | "disabled";

export interface Measurement {
  point_id: string;
  label: string;
  source: string;
  value: number;
  unit: string;
  source_timestamp: string;
  receive_timestamp: string;
  quality: DataQuality;
  synthetic: boolean;
}

export interface Heartbeat {
  component: string;
  observed_at: string;
  status: ComponentStatus;
  synthetic: boolean;
}

export interface CurrentState {
  observed_at: string;
  freshness: Freshness;
  measurement: Measurement | null;
  heartbeat: Heartbeat | null;
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function isOneOf<T extends string>(value: unknown, allowed: readonly T[]): value is T {
  return typeof value === "string" && allowed.includes(value as T);
}

function isMeasurement(value: unknown): value is Measurement {
  if (!isRecord(value)) {
    return false;
  }
  return (
    typeof value.point_id === "string" &&
    typeof value.label === "string" &&
    typeof value.source === "string" &&
    typeof value.value === "number" &&
    Number.isFinite(value.value) &&
    typeof value.unit === "string" &&
    typeof value.source_timestamp === "string" &&
    typeof value.receive_timestamp === "string" &&
    isOneOf(value.quality, ["good", "stale", "invalid", "comm_error", "out_of_range"]) &&
    typeof value.synthetic === "boolean"
  );
}

function isHeartbeat(value: unknown): value is Heartbeat {
  if (!isRecord(value)) {
    return false;
  }
  return (
    typeof value.component === "string" &&
    typeof value.observed_at === "string" &&
    isOneOf(value.status, ["healthy", "degraded", "unavailable", "stopped", "disabled"]) &&
    typeof value.synthetic === "boolean"
  );
}

export function parseCurrentState(value: unknown): CurrentState {
  if (
    !isRecord(value) ||
    typeof value.observed_at !== "string" ||
    !isOneOf(value.freshness, ["live", "stale", "unavailable"]) ||
    (value.measurement !== null && !isMeasurement(value.measurement)) ||
    (value.heartbeat !== null && !isHeartbeat(value.heartbeat))
  ) {
    throw new Error("Current-state API returned an invalid response");
  }
  return {
    observed_at: value.observed_at,
    freshness: value.freshness,
    measurement: value.measurement,
    heartbeat: value.heartbeat,
  };
}

export async function fetchCurrentState(
  client: ApiClient,
  signal?: AbortSignal,
): Promise<CurrentState> {
  const response = await client.get(
    "/v1/current-state",
    signal === undefined ? undefined : { signal },
  );
  if (!response.ok) {
    throw new Error(`Current-state request failed (${String(response.status)})`);
  }
  return parseCurrentState(await response.json());
}
