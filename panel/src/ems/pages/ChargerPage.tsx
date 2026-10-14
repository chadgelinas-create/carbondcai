import { Button, NumberInput, TextInput, Toggle } from "@carbon/react";
import { useState } from "react";

import type { ChargerStatus, ConfigItem, TelemetryPoint } from "../api";
import { ConfigTable, displayValue, PointTable } from "./shared";

interface ChargerPageProps {
  byId: Map<string, TelemetryPoint>;
  charger: ChargerStatus | null;
  config: ConfigItem[];
  onApply: (groupId: string, key: string, value: number | string) => Promise<void>;
  onPort: (portId: string, occupied: boolean, requestedKw: number) => Promise<void>;
  canWrite?: boolean;
}

const DEFAULT_PORTS = ["1", "2", "3", "4", "5", "6"].map((port_id) => ({
  port_id,
  label: port_id,
  occupied: false,
  requested_kw: 0,
  allocated_kw: 0,
}));

export function ChargerPage({
  byId,
  charger,
  config,
  onApply,
  onPort,
  canWrite = true,
}: ChargerPageProps) {
  const limited = config.find((item) => item.key === "soc_limited");
  const [threshold, setThreshold] = useState(limited?.value ?? 0.15);
  const [message, setMessage] = useState(
    config.find((item) => item.key === "station_message")?.value_text ?? "",
  );
  const [asks, setAsks] = useState<Record<string, number>>({
    "1": 350,
    "2": 200,
    "3": 300,
    "4": 60,
    "5": 100,
    "6": 60,
  });
  const [busy, setBusy] = useState<string | null>(null);
  const points = [...byId.values()].filter((point) => point.device_id === "dc600");
  const ports = charger?.ports?.length ? charger.ports : DEFAULT_PORTS;

  const runPort = async (portId: string, occupied: boolean, requestedKw: number) => {
    setBusy(portId);
    try {
      await onPort(portId, occupied, requestedKw);
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="ems-page" aria-labelledby="charger-title">
      <div className="page-title">
        <h1 id="charger-title">EV Charge Controller</h1>
        <p>
          Appendix A holdings: PMS reads 40001 demand (INT32 W, big-endian) and writes
          40101 Available Power. The charger shares ports internally (sat 600/300/300 kW,
          500 A/cable). SOC gates 15% / 10% / 22%. No per-port PMS registers.
        </p>
      </div>
      <div className="summary-grid">
        <article className="cds--tile">
          <span>Mode</span>
          <strong>{charger?.mode ?? "—"}</strong>
        </article>
        <article className="cds--tile">
          <span>SOC</span>
          <strong>{charger ? `${(charger.soc * 100).toFixed(1)}%` : "—"}</strong>
        </article>
        <article className="cds--tile">
          <span>Demand</span>
          <strong>{(charger?.demand_kw ?? 0).toFixed(0)} kW</strong>
        </article>
        <article className="cds--tile">
          <span>Available</span>
          <strong>{(charger?.available_kw ?? 0).toFixed(0)} kW</strong>
        </article>
        <article className="cds--tile">
          <span>Unmet</span>
          <strong>{(charger?.unmet_kw ?? 0).toFixed(0)} kW</strong>
        </article>
        <article className="cds--tile">
          <span>Handshake</span>
          <strong>{charger?.handshake ?? "idle"}</strong>
        </article>
        <article className="cds--tile">
          <span>PMS status</span>
          <strong>
            {charger?.pms_status ?? 0} · {charger?.pms_status_label ?? "normal"}
          </strong>
        </article>
        <article className="cds--tile">
          <span>Time remaining</span>
          <strong>
            {charger?.time_remaining_min === 999
              ? "unlimited"
              : `${charger?.time_remaining_min ?? 0} min`}
          </strong>
        </article>
      </div>
      <p className="ems-station-message">{charger?.driver_message}</p>
      <div className="ems-actions">
        <Button
          size="sm"
          disabled={!canWrite || !charger?.allow_new_sessions || busy !== null}
          onClick={() => void runPort("1", true, 350)}
        >
          Car arrives port 1 @ 350 kW
        </Button>
        <Button
          size="sm"
          kind="secondary"
          disabled={!canWrite || !charger?.allow_new_sessions || busy !== null}
          onClick={() => void runPort("3", true, 300)}
        >
          Car arrives port 3 @ 300 kW
        </Button>
      </div>
      <div className="summary-grid">
        {ports.map((port) => (
          <article className="cds--tile ems-port-card" key={port.port_id}>
            <span>
              Port {port.port_id} · {port.label}
            </span>
            <strong>
              {port.occupied
                ? `${port.allocated_kw.toFixed(0)} / ${port.requested_kw.toFixed(0)} kW · ${(port.energy_kwh ?? 0).toFixed(2)} kWh${port.capped ? " · capped" : ""}`
                : "empty"}
            </strong>
            <Toggle
              id={`port-${port.port_id}-occ`}
              size="sm"
              labelA="Empty"
              labelB="Car plugged in"
              toggled={port.occupied}
              disabled={!canWrite || busy !== null}
              onToggle={(occupied) => {
                const ask = asks[port.port_id] ?? 60;
                void runPort(port.port_id, occupied, occupied ? ask : 0);
              }}
            />
            <NumberInput
              id={`port-${port.port_id}-kw`}
              label="Request kW"
              min={30}
              max={600}
              step={10}
              value={asks[port.port_id] ?? 60}
              disabled={!canWrite || !port.occupied || busy !== null}
              onChange={(_event, { value }) => {
                setAsks((current) => ({ ...current, [port.port_id]: Number(value) }));
              }}
            />
            <Button
              size="sm"
              kind="tertiary"
              disabled={!canWrite || !port.occupied || busy !== null}
              onClick={() => void runPort(port.port_id, true, asks[port.port_id] ?? 60)}
            >
              Ask
            </Button>
          </article>
        ))}
      </div>
      {charger?.sessions?.length ? (
        <ol className="ems-event-log">
          {charger.sessions.map((session) => (
            <li key={session.id}>
              session {session.id} port {session.port_id} {session.status} {session.energy_kwh.toFixed(2)}{" "}
              kWh @ {session.power_kw.toFixed(0)} kW
            </li>
          ))}
        </ol>
      ) : null}
      {charger?.events?.length ? (
        <ol className="ems-event-log">
          {charger.events.map((event, index) => (
            <li key={`${index}-${event}`}>{event}</li>
          ))}
        </ol>
      ) : null}
      {canWrite ? (
        <>
          <div className="ems-config-row">
            <NumberInput
              id="soc-limited"
              label="Unavailable SOC threshold (15% default)"
              value={threshold}
              step={0.01}
              min={0.05}
              max={0.5}
              onChange={(_event, { value }) => {
                setThreshold(Number(value));
              }}
            />
            <Button
              onClick={() => {
                void onApply("charger", "soc_limited", threshold).then(() =>
                  onApply("charger", "soc_maintenance", threshold),
                );
              }}
            >
              Apply threshold
            </Button>
          </div>
          <div className="ems-config-row">
            <TextInput
              id="station-message"
              labelText="Station driver message"
              value={message}
              onChange={(event) => {
                setMessage(event.target.value);
              }}
            />
            <Button
              onClick={() => {
                void onApply("charger", "station_message", message);
              }}
            >
              Apply message
            </Button>
          </div>
        </>
      ) : null}
      <ConfigTable items={config} onApply={canWrite ? onApply : undefined} />
      <PointTable title="DC600 points" points={points} />
    </section>
  );
}
