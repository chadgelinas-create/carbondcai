import { useEffect, useState } from "react";
import {
  Button,
  Form,
  InlineLoading,
  InlineNotification,
  NumberInput,
  Select,
  SelectItem,
  Tag,
  TextInput,
  Tile,
} from "@carbon/react";
import type { ApiClient } from "./api/client";
import {
  parseMonitor,
  streamUrl,
  type Connection,
  type MonitorState,
  type SavedConfig,
} from "./api/monitor";

const labels = {
  not_configured: "Connection not set up",
  connecting: "Connecting",
  connected: "Connected",
  disconnected: "Disconnected",
  stale: "Reading out of date",
  invalid: "Reading unavailable",
  storage_error: "Database unavailable",
};

export function Monitor({
  client,
  apiBaseUrl,
  admin,
}: {
  client: ApiClient;
  apiBaseUrl: string;
  admin: boolean;
}) {
  const [state, setState] = useState<MonitorState | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [offline, setOffline] = useState(false);
  useEffect(() => {
    const socket = new WebSocket(streamUrl(apiBaseUrl));
    let lastMessage = Date.now();
    let limit = 10000;
    let expiryTimer: number | undefined;
    socket.onmessage = (event) => {
      window.clearTimeout(expiryTimer);
      try {
        const next = parseMonitor(JSON.parse(String(event.data)));
        lastMessage = Date.now();
        limit = Math.max(3000, next.stale_seconds * 1000);
        setState(next);
        setOffline(false);
        setError("");
        const reading = next.state.measurement;
        if (next.connection === "connected" && reading !== null) {
          const age = Date.parse(next.state.observed_at) - Date.parse(reading.receive_timestamp);
          expiryTimer = window.setTimeout(
            () => {
              setState({
                ...next,
                connection: "stale",
                message: "The last reading is out of date. Waiting for new data.",
              });
            },
            Math.max(0, next.stale_seconds * 1000 - age),
          );
        }
      } catch {
        setState(null);
        setError("The server sent an invalid battery reading.");
      }
    };
    socket.onclose = (event) => {
      if (event.code === 1008) window.dispatchEvent(new Event("session-ended"));
      setOffline(true);
      setError(
        event.code === 1008
          ? "Your session ended or access was denied. Reload to sign in."
          : "The live connection was lost. Reconnect to receive readings.",
      );
    };
    socket.onerror = () => {
      setOffline(true);
      setError("Cannot connect to live updates.");
    };
    const timer = window.setInterval(() => {
      if (Date.now() - lastMessage > limit) {
        setOffline(true);
        setError("Live updates stopped. Reconnect to receive readings.");
      }
    }, 1000);
    return () => {
      window.clearTimeout(expiryTimer);
      window.clearInterval(timer);
      socket.onclose = null;
      socket.onerror = null;
      socket.onmessage = null;
      socket.close();
    };
  }, [apiBaseUrl, retry]);

  const connection = offline ? "disconnected" : state?.connection;
  const reading = state?.state.measurement;
  const valid =
    connection === "connected" && state?.state.freshness === "live" && reading?.quality === "good";
  return (
    <section className="lab" aria-labelledby="monitor-title">
      <h1 id="monitor-title">Battery monitoring</h1>
      <p>Battery readings received through the selected Modbus connection.</p>
      {error && <InlineNotification kind="error" title={error} hideCloseButton />}
      {offline && (
        <Button
          onClick={() => {
            setState(null);
            setError("");
            setOffline(false);
            setRetry((value) => value + 1);
          }}
        >
          Reconnect
        </Button>
      )}
      <Tile>
        <h2>Battery charge</h2>
        {connection === undefined ? (
          <InlineLoading description="Waiting for a battery reading…" />
        ) : (
          <>
            <p>
              {connection === "disconnected"
                ? "0%"
                : valid
                  ? `${(reading.value * 100).toFixed(1)}%`
                  : "Unavailable"}
            </p>
            <Tag type={connection === "connected" ? "green" : "gray"}>{labels[connection]}</Tag>
          </>
        )}
        <p>
          Data source:{" "}
          {state?.mode === "sim"
            ? "Simulation (SIM)"
            : state?.mode === "live"
              ? "Live equipment (LIVE)"
              : "Not selected"}
        </p>
        {connection === "disconnected" ? (
          <p>Zero means the connection is unavailable. It is not a measured battery level.</p>
        ) : (
          <p>{state?.message}</p>
        )}
        {valid && (
          <p>Last reading: {new Date(reading.receive_timestamp).toLocaleString()} (local time)</p>
        )}
      </Tile>
      {admin ? (
        <ConnectionForm client={client} />
      ) : (
        <p>An administrator can change the connection settings.</p>
      )}
    </section>
  );
}

function ConnectionForm({ client }: { client: ApiClient }) {
  const [saved, setSaved] = useState<SavedConfig | null>(null);
  const [name, setName] = useState("Battery connection");
  const [reason, setReason] = useState("");
  const [mode, setMode] = useState<"sim" | "live">("sim");
  const [host, setHost] = useState("");
  const [port, setPort] = useState("");
  const [unitId, setUnitId] = useState("");
  const [poll, setPoll] = useState("");
  const [timeout, setTimeoutValue] = useState("");
  const [stale, setStale] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  function fillConnection(connection: Connection | undefined) {
    setHost(connection?.host ?? "");
    setPort(connection ? String(connection.port) : "");
    setUnitId(connection ? String(connection.unit_id) : "");
    setPoll(connection ? String(connection.poll_seconds) : "");
    setTimeoutValue(connection ? String(connection.timeout_seconds) : "");
    setStale(connection ? String(connection.stale_seconds) : "");
  }
  useEffect(() => {
    const controller = new AbortController();
    void client
      .get("/v1/config", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("Could not load saved settings. Reload to try again.");
        const value = (await response.json()) as SavedConfig;
        setSaved(value);
        if (value.active !== null) {
          const connection = value.connections[value.active];
          if (connection) {
            setMode(connection.mode);
            fillConnection(connection);
          }
        }
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setError(reason instanceof Error ? reason.message : "Could not load settings.");
      });
    return () => {
      controller.abort();
    };
  }, [client]);
  async function save() {
    if (saved === null) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const response = await client.post("/v1/configuration/monitor/drafts", {
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          reason,
          expected_generation: saved.revision,
          settings: {
            revision: saved.revision,
            device_id: "ba1-1",
            mode,
            host,
            port: Number(port),
            unit_id: Number(unitId),
            poll_seconds: Number(poll),
            timeout_seconds: Number(timeout),
            stale_seconds: Number(stale),
          },
        }),
      });
      const body = (await response.json()) as SavedConfig & { detail?: unknown };
      if (!response.ok)
        throw new Error(
          typeof body.detail === "string"
            ? body.detail
            : "Check the connection fields and timing values.",
        );
      setMessage("Saved for review. Open Settings history to approve and apply this connection.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Settings could not be saved.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Tile>
      <h2>Connection settings</h2>
      <p>
        Propose a battery connection. Your current connection stays in use until an administrator
        approves and applies the change.
      </p>
      {error && <InlineNotification kind="error" title={error} hideCloseButton />}
      {message && <InlineNotification kind="success" title={message} hideCloseButton />}
      <Form
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <div className="form-grid">
          <TextInput
            id="connection-name"
            labelText="Change name"
            value={name}
            maxLength={120}
            required
            disabled={busy}
            onChange={(event) => {
              setName(event.target.value);
            }}
          />
          <TextInput
            id="connection-reason"
            labelText="Reason for change"
            value={reason}
            maxLength={1000}
            required
            disabled={busy}
            onChange={(event) => {
              setReason(event.target.value);
            }}
          />
          <Select
            id="source"
            labelText="Data source"
            value={mode}
            disabled={busy}
            onChange={(event) => {
              const selected = event.target.value as "sim" | "live";
              setMode(selected);
              fillConnection(saved?.connections[`ba1-1:${selected}`]);
              setMessage("");
            }}
          >
            <SelectItem value="sim" text="Simulation (SIM)" />
            <SelectItem value="live" text="Live equipment (LIVE)" />
          </Select>
          <TextInput
            id="device-address"
            labelText="Device address"
            helperText="An IP address or hostname, without http://"
            required
            value={host}
            disabled={busy}
            onChange={(event) => {
              setHost(event.target.value);
            }}
          />
          {(
            [
              ["port", "Port", port, setPort, 1, 65535, 1],
              ["unit-id", "Unit ID", unitId, setUnitId, 1, 247, 1],
              ["read-interval", "Read interval (seconds)", poll, setPoll, 0.1, 3600, 0.1],
              [
                "connection-timeout",
                "Connection timeout (seconds)",
                timeout,
                setTimeoutValue,
                0.1,
                60,
                0.1,
              ],
              ["stale-time", "Reading expiry (seconds)", stale, setStale, 0.2, 86400, 0.1],
            ] as const
          ).map(([id, label, value, change, min, max, step]) => (
            <NumberInput
              key={id}
              id={id}
              label={label}
              value={value}
              min={min}
              max={max}
              step={step}
              required
              disabled={busy}
              allowEmpty
              hideSteppers
              onChange={(_, data) => {
                change(String(data.value));
              }}
            />
          ))}
        </div>
        <p>
          Unit ID is the device number used for Modbus. Reading expiry must exceed the read interval
          plus the connection timeout.
        </p>
        <Button type="submit" disabled={busy || saved === null}>
          {busy ? "Saving…" : "Save for review"}
        </Button>
      </Form>
    </Tile>
  );
}
