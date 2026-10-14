import { Button, NumberInput } from "@carbon/react";
import { useState } from "react";

import type { ConfigAuditRow, ConfigItem, TelemetryPoint } from "../api";
import { CommandOutcomeTable, ConfigTable, displayValue, PointTable } from "./shared";

interface StabilitiPageProps {
  byId: Map<string, TelemetryPoint>;
  config: ConfigItem[];
  audit: ConfigAuditRow[];
  lastCommand: ConfigItem | null;
  onApply: (groupId: string, key: string, value: number | string) => Promise<void>;
  onFault?: (deviceId: string, clear: boolean) => Promise<void>;
  canWrite?: boolean;
}

const UNITS = ["st-4", "st-5", "st-6"] as const;
const LIMIT_KEYS = {
  "st-4": "st4_power_limit",
  "st-5": "st5_power_limit",
  "st-6": "st6_power_limit",
} as const;
const OPS = ["offline", "ready", "enabled", "derated", "faulted", "recovering"] as const;

function opLabel(
  point: TelemetryPoint | undefined,
  comms: TelemetryPoint | undefined,
): string {
  if (!comms || comms.stale || comms.value < 0.5) {
    return "offline";
  }
  if (!point || point.stale) {
    return "offline";
  }
  const index = Math.round(point.value);
  return OPS[index] ?? "offline";
}

function acKw(byId: Map<string, TelemetryPoint>, unit: string): number {
  const point = byId.get(`${unit}.ac.port.power`) ?? byId.get(`${unit}.actual.power`);
  if (!point || point.stale) {
    return 0;
  }
  return Number(point.value);
}

export function StabilitiPage({
  byId,
  config,
  audit,
  lastCommand,
  onApply,
  onFault,
  canWrite = true,
}: StabilitiPageProps) {
  const [limits, setLimits] = useState<Record<string, number>>(() => ({
    "st-4": config.find((item) => item.key === "st4_power_limit")?.value ?? 60,
    "st-5": config.find((item) => item.key === "st5_power_limit")?.value ?? 60,
    "st-6": config.find((item) => item.key === "st6_power_limit")?.value ?? 60,
  }));
  const [siteLoad, setSiteLoad] = useState(
    config.find((item) => item.key === "site_ac_load")?.value ?? 0,
  );
  const points = [...byId.values()].filter((point) => point.group_id === "stabiliti");
  const acTotal = UNITS.reduce((sum, unit) => sum + acKw(byId, unit), 0);
  const ready = UNITS.filter(
    (unit) =>
      opLabel(byId.get(`${unit}.operational.state`), byId.get(`${unit}.communication.ok`)) !==
      "offline",
  ).length;
  const derated = UNITS.filter(
    (unit) =>
      opLabel(byId.get(`${unit}.operational.state`), byId.get(`${unit}.communication.ok`)) ===
      "derated",
  ).length;
  return (
    <section className="ems-page" aria-labelledby="stabiliti-title">
      <div className="page-title">
        <h1 id="stabiliti-title">Stabiliti ST-4/5/6</h1>
        <p>
          Grid-forming 480 V AC from the 750 V bus. No PV on these units. CET Rev 15 reads are
          logical SIM values — register width is still not promoted.
        </p>
      </div>
      <div className="summary-grid">
        <article className="cds--tile">
          <span>Fleet online</span>
          <strong>
            {ready}/{UNITS.length}
          </strong>
        </article>
        <article className="cds--tile">
          <span>Derated</span>
          <strong>{derated}</strong>
        </article>
        <article className="cds--tile">
          <span>AC total</span>
          <strong>{acTotal.toFixed(1)} kW</strong>
        </article>
      </div>
      <div className="summary-grid">
        {UNITS.map((unit) => (
          <article className="cds--tile ems-port-card" key={unit}>
            <span>{unit.toUpperCase()}</span>
            <strong>
              {opLabel(
                byId.get(`${unit}.operational.state`),
                byId.get(`${unit}.communication.ok`),
              )}
            </strong>
            <span>AC {displayValue(byId.get(`${unit}.ac.port.power`) ?? byId.get(`${unit}.actual.power`))} kW</span>
            <span>DC in {displayValue(byId.get(`${unit}.dc.in.power`))} kW</span>
            <span>
              {displayValue(byId.get(`${unit}.ac.port.volts`))} V ·{" "}
              {displayValue(byId.get(`${unit}.ac.port.current`))} A
            </span>
            <span>{displayValue(byId.get(`${unit}.temperature.maximum`))} °C</span>
            {canWrite ? (
              <>
                <NumberInput
                  id={`${unit}-limit`}
                  label={`${unit.toUpperCase()} limit (kW)`}
                  value={limits[unit] ?? 60}
                  min={0}
                  max={200}
                  onChange={(_event, { value }) => {
                    setLimits((current) => ({ ...current, [unit]: Number(value) }));
                  }}
                />
                <Button
                  size="sm"
                  onClick={() => {
                    void onApply("stabiliti", LIMIT_KEYS[unit], limits[unit] ?? 60);
                  }}
                >
                  Apply {unit.toUpperCase()}
                </Button>
              </>
            ) : null}
            {onFault ? (
              <>
                <Button
                  size="sm"
                  kind="danger--tertiary"
                  onClick={() => {
                    void onFault(unit, false);
                  }}
                >
                  Comms loss
                </Button>
                <Button
                  size="sm"
                  kind="ghost"
                  onClick={() => {
                    void onFault(unit, true);
                  }}
                >
                  Recover
                </Button>
              </>
            ) : null}
          </article>
        ))}
      </div>
      {canWrite ? (
        <div className="ems-config-row">
          <NumberInput
            id="site-ac-load"
            label="Site AC load (kW, split across online units)"
            value={siteLoad}
            min={0}
            max={180}
            onChange={(_event, { value }) => {
              setSiteLoad(Number(value));
            }}
          />
          <Button
            onClick={() => {
              void onApply("stabiliti", "site_ac_load", siteLoad);
            }}
          >
            Apply site load
          </Button>
        </div>
      ) : null}
      <CommandOutcomeTable
        last={lastCommand?.group_id === "stabiliti" ? lastCommand : null}
        audit={audit}
      />
      <ConfigTable items={config} onApply={canWrite ? onApply : undefined} />
      <PointTable title="Stabiliti points" points={points} />
    </section>
  );
}
