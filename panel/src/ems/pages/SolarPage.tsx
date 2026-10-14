import { Button, NumberInput } from "@carbon/react";
import { useState } from "react";

import type { ConfigAuditRow, ConfigItem, TelemetryPoint } from "../api";
import { CommandOutcomeTable, ConfigTable, displayValue, PointTable } from "./shared";

interface SolarPageProps {
  byId: Map<string, TelemetryPoint>;
  config: ConfigItem[];
  audit: ConfigAuditRow[];
  lastCommand: ConfigItem | null;
  onApply: (groupId: string, key: string, value: number | string) => Promise<void>;
  canWrite?: boolean;
}

const MPPT_KEYS = [
  ["mppt_hold", "Hold"],
  ["mppt_resume", "Resume"],
  ["mppt_taper", "Taper"],
  ["mppt_off", "Off"],
  ["mppt_restart", "Restart"],
] as const;

export function SolarPage({
  byId,
  config,
  audit,
  lastCommand,
  onApply,
  canWrite = true,
}: SolarPageProps) {
  const [drafts, setDrafts] = useState<Record<string, number>>(() => {
    const initial: Record<string, number> = {};
    for (const [key] of MPPT_KEYS) {
      initial[key] = config.find((item) => item.key === key)?.value ?? 0;
    }
    return initial;
  });
  const points = [...byId.values()].filter(
    (point) => point.group_id === "solar" || point.device_id === "cnvtr-2",
  );
  return (
    <section className="ems-page" aria-labelledby="solar-title">
      <div className="page-title">
        <h1 id="solar-title">Solar / MPPT</h1>
        <p>CNVTR-2 PV path. MPPT setpoints write back into the simulator from battery SOC.</p>
      </div>
      <div className="summary-grid">
        <article className="cds--tile">
          <span>PV available</span>
          <strong>{displayValue(byId.get("cnvtr2.available.power"))} kW</strong>
        </article>
        <article className="cds--tile">
          <span>Accepted</span>
          <strong>{displayValue(byId.get("cnvtr2.test.accepted.power"))} kW</strong>
        </article>
        <article className="cds--tile">
          <span>MPPT applying</span>
          <strong>
            {(byId.get("cnvtr2.pv.mppt.applying")?.value ?? 0) >= 0.5 ? "yes" : "no"}
          </strong>
        </article>
        <article className="cds--tile">
          <span>Module temp</span>
          <strong>{displayValue(byId.get("cnvtr2.temperature.module"))} °C</strong>
        </article>
      </div>
      {canWrite
        ? MPPT_KEYS.map(([key, label]) => (
            <div className="ems-config-row" key={key}>
              <NumberInput
                id={`mppt-${key}`}
                label={`${label} threshold (ratio)`}
                value={drafts[key] ?? 0}
                step={0.01}
                min={0.2}
                max={1}
                onChange={(_event, { value }) => {
                  setDrafts((current) => ({ ...current, [key]: Number(value) }));
                }}
              />
              <Button
                onClick={() => {
                  void onApply("solar", key, drafts[key] ?? 0);
                }}
              >
                Apply {label}
              </Button>
            </div>
          ))
        : null}
      <CommandOutcomeTable last={lastCommand?.group_id === "solar" ? lastCommand : null} audit={audit} />
      <ConfigTable items={config} onApply={canWrite ? onApply : undefined} />
      <PointTable title="Solar points" points={points} />
    </section>
  );
}
