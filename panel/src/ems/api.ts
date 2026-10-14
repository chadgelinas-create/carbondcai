export interface AuthSession {
  token: string;
  username: string;
  role: string;
  email?: string;
  display_name?: string;
  company?: string;
  system_name?: string;
  active?: boolean;
}

export interface StoredAccount {
  username: string;
  role: string;
  email: string;
  display_name: string;
  company: string;
  system_name: string;
  active: boolean;
  invite_sent?: boolean;
  invite_url?: string;
  mail_status?: string;
}

export interface InvitePreview {
  username: string;
  email: string;
  display_name: string;
  purpose: string;
}

export interface AccountActivity {
  occurred_at: string;
  actor: string;
  action: string;
  subject: string;
  gate: string;
  detail: string;
}

export interface TelemetryPoint {
  point_id: string;
  device_id: string;
  group_id: string;
  label: string;
  value: number;
  unit: string;
  quality: string;
  source_timestamp: string;
  stale: boolean;
}

export interface ConfigItem {
  group_id: string;
  key: string;
  value: number | null;
  value_text: string | null;
  unit: string;
  updated_at: string;
  updated_by: string;
  outcome?: string;
  requested?: string | null;
  effective?: string | null;
  message?: string;
}

export interface ConfigAuditRow {
  group_id: string;
  key: string;
  old_value: string | null;
  new_value: string;
  changed_by: string;
  changed_at: string;
}

export interface SupervisorStatus {
  bus_voltage: number;
  system_status: number;
  available_power_kw: number;
  heartbeats_ok: number;
  heartbeats_total: number;
  status_label: string;
  heartbeats: Record<string, boolean>;
}

export interface ChargerPort {
  port_id: string;
  label: string;
  occupied: boolean;
  requested_kw: number;
  allocated_kw: number;
  energy_kwh?: number;
  session_id?: number | null;
  capped?: boolean;
}

export interface ChargerSession {
  id: number;
  port_id: string;
  status: string;
  energy_kwh: number;
  power_kw: number;
  capped: boolean;
  started_at: string;
  ended_at: string | null;
}

export interface ChargerStatus {
  mode: string;
  allow_new_sessions: boolean;
  energy_cap_kwh: number | null;
  terminate_active: boolean;
  driver_message: string;
  soc: number;
  demand_kw?: number;
  available_kw?: number;
  unmet_kw?: number;
  ports?: ChargerPort[];
  events?: string[];
  handshake?: string;
  sessions?: ChargerSession[];
  pms_status?: number;
  pms_status_label?: string;
  time_remaining_min?: number;
  pv_kw?: number;
}

import type { ApiClient } from "../api/client";

function authHeaders(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
}

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    return body.detail ?? response.statusText;
  } catch {
    return response.statusText;
  }
}

export async function requestPasswordReset(
  client: ApiClient,
  email: string,
): Promise<string> {
  const response = await client.post("/v1/auth/forgot-password", {
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  const body = (await response.json()) as { mail_status?: string };
  return body.mail_status ?? "If that email is authorized, we sent a login link. Check your email.";
}

export async function login(
  client: ApiClient,
  username: string,
  password: string,
): Promise<AuthSession> {
  const response = await client.post("/v1/auth/login", {
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return (await response.json()) as AuthSession;
}

export async function exchangeLabSession(client: ApiClient): Promise<AuthSession> {
  const response = await client.get("/v1/auth/from-lab");
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return (await response.json()) as AuthSession;
}

export async function listActivity(client: ApiClient, token: string): Promise<AccountActivity[]> {
  const response = await client.get("/v1/auth/activity", { headers: authHeaders(token) });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return (await response.json()) as AccountActivity[];
}

export async function listUsers(client: ApiClient, token: string): Promise<StoredAccount[]> {
  const response = await client.get("/v1/auth/users", { headers: authHeaders(token) });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return (await response.json()) as StoredAccount[];
}

export async function createUser(
  client: ApiClient,
  token: string,
  body: {
    username: string;
    password?: string;
    email: string;
    display_name: string;
    company: string;
    system_name: string;
    role: string;
  },
): Promise<StoredAccount> {
  const response = await client.post("/v1/auth/users", {
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return (await response.json()) as StoredAccount;
}

export async function sendUserInvite(
  client: ApiClient,
  token: string,
  username: string,
): Promise<StoredAccount> {
  const response = await client.post(`/v1/auth/users/${encodeURIComponent(username)}/invite`, {
    headers: authHeaders(token),
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return (await response.json()) as StoredAccount;
}

export async function readInvite(client: ApiClient, token: string): Promise<InvitePreview> {
  const response = await client.get(`/v1/auth/invite?token=${encodeURIComponent(token)}`);
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return (await response.json()) as InvitePreview;
}

export async function acceptInvite(
  client: ApiClient,
  token: string,
  password: string,
): Promise<AuthSession> {
  const response = await client.post("/v1/auth/invite", {
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, password }),
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return (await response.json()) as AuthSession;
}

export async function setUserActive(
  client: ApiClient,
  token: string,
  username: string,
  active: boolean,
): Promise<StoredAccount> {
  const response = await client.post(`/v1/auth/users/${encodeURIComponent(username)}/active`, {
    headers: authHeaders(token),
    body: JSON.stringify({ active }),
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return (await response.json()) as StoredAccount;
}

export async function fetchTelemetry(
  client: ApiClient,
  token: string,
  groupId: string,
): Promise<TelemetryPoint[]> {
  const response = await client.get(`/v1/ems/telemetry/${groupId}`, {
    headers: authHeaders(token),
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return (await response.json()) as TelemetryPoint[];
}

export async function fetchConfig(
  client: ApiClient,
  token: string,
  groupId: string,
): Promise<ConfigItem[]> {
  const response = await client.get(`/v1/ems/config/${groupId}`, {
    headers: authHeaders(token),
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return (await response.json()) as ConfigItem[];
}

export async function fetchConfigAudit(
  client: ApiClient,
  token: string,
  groupId: string,
): Promise<ConfigAuditRow[]> {
  const response = await client.get(`/v1/ems/config/${groupId}/audit`, {
    headers: authHeaders(token),
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return (await response.json()) as ConfigAuditRow[];
}

export async function writeConfig(
  client: ApiClient,
  token: string,
  groupId: string,
  key: string,
  value: number | string,
): Promise<ConfigItem> {
  const response = await client.post(`/v1/ems/config/${groupId}`, {
    headers: authHeaders(token),
    body: JSON.stringify({ key, value }),
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return (await response.json()) as ConfigItem;
}

export async function fetchMode(
  client: ApiClient,
  token: string,
): Promise<"sim" | "live"> {
  const response = await client.get("/v1/ems/mode", { headers: authHeaders(token) });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  const body = (await response.json()) as { data_source: "sim" | "live" };
  return body.data_source;
}

export async function setMode(
  client: ApiClient,
  token: string,
  dataSource: "sim" | "live",
): Promise<"sim" | "live"> {
  const result = await client.put("/v1/ems/mode", {
    headers: authHeaders(token),
    body: JSON.stringify({ data_source: dataSource }),
  });
  if (!result.ok) {
    throw new Error(await readError(result));
  }
  const body = (await result.json()) as { data_source: "sim" | "live" };
  return body.data_source;
}

export async function fetchSupervisor(
  client: ApiClient,
  token: string,
): Promise<SupervisorStatus> {
  const response = await client.get("/v1/ems/supervisor", { headers: authHeaders(token) });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return (await response.json()) as SupervisorStatus;
}

export async function fetchCharger(
  client: ApiClient,
  token: string,
): Promise<ChargerStatus> {
  const response = await client.get("/v1/ems/charger/status", {
    headers: authHeaders(token),
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return (await response.json()) as ChargerStatus;
}

export async function setChargerPort(
  client: ApiClient,
  token: string,
  portId: string,
  occupied: boolean,
  requestedKw: number,
): Promise<ChargerStatus> {
  const response = await client.post(`/v1/ems/charger/ports/${portId}`, {
    headers: authHeaders(token),
    body: JSON.stringify({ occupied, requested_kw: requestedKw }),
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return (await response.json()) as ChargerStatus;
}

export async function injectCommsLoss(
  client: ApiClient,
  token: string,
  deviceId: string,
  clear = false,
): Promise<void> {
  const path = `/v1/ems/demo/comms-loss/${deviceId}?clear=${clear ? "true" : "false"}`;
  const response = await client.post(path, { headers: authHeaders(token) });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
}

export function connectEmsSocket(
  apiBaseUrl: string,
  token: string,
  onMessage: (payload: unknown) => void,
): WebSocket {
  const url = new URL(apiBaseUrl || "/", window.location.origin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = `${url.pathname.replace(/\/$/, "")}/v1/ems/ws`;
  url.search = `token=${encodeURIComponent(token)}`;
  const socket = new WebSocket(url);
  socket.addEventListener("message", (event) => {
    onMessage(JSON.parse(String(event.data)));
  });
  return socket;
}
