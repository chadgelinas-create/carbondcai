import { Button, NumberInput } from "@carbon/react";
import { useState } from "react";

import type { ConfigItem, TelemetryPoint } from "../api";
import { ConfigTable, displayValue, PointTable } from "./shared";

interface BessPageProps {
  byId: Map<string, TelemetryPoint>;
  config: ConfigItem[];
  onApply: (groupId: string, key: string, value: number | string) => Promise<void>;
  canWrite?: boolean;
}

const BMS_POINTS = [
  "bess.cluster.soc",
  "bess.cluster.voltage",
  "bess.cluster.current",
  "bess.cluster.temperature.maximum",
  "bess.cluster.limit.charge.power",
  "bess.cluster.limit.discharge.power",
  "bess.cluster.contactor.main",
  "bess.cluster.contactor.precharge",
];

export function BessPage({ byId, config, onApply, canWrite = true }: BessPageProps) {
  const packMax = config.find((item) => item.key === "pack_max_voltage");
  const [draft, setDraft] = useState<number>(packMax?.value ?? 730);

  return (
    <section className="ems-page" aria-labelledby="bess-title">
      <div className="page-title">
        <h1 id="bess-title">BESS / BMS</h1>
        <p>Live BA1-1 telemetry and app-owned pack voltage limits.</p>
      </div>
      <div className="summary-grid">
        <article className="cds--tile">
          <span>SOC</span>
          <strong>{displayValue(byId.get("bess.cluster.soc"), true)}</strong>
        </article>
        <article className="cds--tile">
          <span>Pack voltage</span>
          <strong>{displayValue(byId.get("bess.cluster.voltage"))} V</strong>
        </article>
        <article className="cds--tile">
          <span>Pack current</span>
          <strong>{displayValue(byId.get("bess.cluster.current"))} A</strong>
        </article>
        <article className="cds--tile">
          <span>Max cell temp</span>
          <strong>{displayValue(byId.get("bess.cluster.temperature.maximum"))} °C</strong>
        </article>
      </div>
      {canWrite ? (
        <div className="ems-config-row">
          <NumberInput
            id="pack-max-voltage"
            label="Pack max voltage (V)"
            value={draft}
            onChange={(_event, { value }) => {
              setDraft(Number(value));
            }}
            min={600}
            max={900}
            step={1}
          />
          <Button
            onClick={() => {
              void onApply("bms", "pack_max_voltage", draft);
            }}
          >
            Apply
          </Button>
        </div>
      ) : null}
      <ConfigTable items={config} onApply={canWrite ? onApply : undefined} />
      <PointTable
        title="BMS registers"
        points={BMS_POINTS.map((id) => byId.get(id)).filter(Boolean) as TelemetryPoint[]}
      />
    </section>
  );
}
