import { Button, Tag } from "@carbon/react";

import type { ChargerStatus, SupervisorStatus, TelemetryPoint } from "../api";
import { displayValue, PointTable } from "./shared";

interface OverviewPageProps {
  byId: Map<string, TelemetryPoint>;
  supervisor: SupervisorStatus | null;
  charger: ChargerStatus | null;
  onFault?: (deviceId: string, clear: boolean) => Promise<void>;
}

export function OverviewPage({ byId, supervisor, charger, onFault }: OverviewPageProps) {
  const soc = displayValue(byId.get("bess.cluster.soc"), true);
  return (
    <section className="ems-page" aria-labelledby="overview-title">
      <div className="page-title">
        <h1 id="overview-title">Overview</h1>
        <p>Whole-skid live KPIs from backend historian / WebSocket — not browser timers.</p>
      </div>
      <div className="summary-grid">
        <article className="cds--tile">
          <span>BESS SOC</span>
          <strong>{soc}</strong>
        </article>
        <article className="cds--tile">
          <span>750 V bus</span>
          <strong>{supervisor ? `${supervisor.bus_voltage.toFixed(1)} V` : "—"}</strong>
        </article>
        <article className="cds--tile">
          <span>System status</span>
          <strong>
            {supervisor ? `${supervisor.system_status} · ${supervisor.status_label}` : "—"}
          </strong>
        </article>
        <article className="cds--tile">
          <span>Heartbeats</span>
          <strong>
            {supervisor
              ? `${supervisor.heartbeats_ok}/${supervisor.heartbeats_total}`
              : "—"}
          </strong>
        </article>
        <article className="cds--tile">
          <span>Available power</span>
          <strong>
            {supervisor ? `${supervisor.available_power_kw.toFixed(1)} kW` : "—"}
          </strong>
        </article>
        <article className="cds--tile">
          <span>Charger mode</span>
          <strong>{charger?.mode ?? "—"}</strong>
        </article>
        <article className="cds--tile">
          <span>AC total (Stabiliti)</span>
          <strong>
            {(
              ["st-4", "st-5", "st-6"] as const
            ).reduce((sum, unit) => {
              const point =
                byId.get(`${unit}.ac.port.power`) ?? byId.get(`${unit}.actual.power`);
              if (!point || point.stale) {
                return sum;
              }
              return sum + Number(point.value);
            }, 0).toFixed(1)}{" "}
            kW
          </strong>
        </article>
        <article className="cds--tile">
          <span>Station message</span>
          <strong>{charger?.driver_message ?? "—"}</strong>
        </article>
      </div>
      {onFault ? (
        <div className="ems-actions">
          <Tag type="blue">Demo fault injection</Tag>
          <Button
            size="sm"
            kind="danger--tertiary"
            onClick={() => {
              void onFault("ba1-1", false);
            }}
          >
            BA1 comms loss
          </Button>
          <Button
            size="sm"
            kind="tertiary"
            onClick={() => {
              void onFault("ba1-1", true);
            }}
          >
            Clear BA1 fault
          </Button>
        </div>
      ) : null}
      <PointTable
        title="Live points"
        points={[...byId.values()].slice(0, 24)}
      />
    </section>
  );
}
