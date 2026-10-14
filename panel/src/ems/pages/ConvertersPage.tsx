import { Button, NumberInput } from "@carbon/react";
import { useState } from "react";

import type { ConfigAuditRow, ConfigItem, TelemetryPoint } from "../api";
import { CommandOutcomeTable, ConfigTable, displayValue, PointTable } from "./shared";

interface ConvertersPageProps {
  byId: Map<string, TelemetryPoint>;
  config: ConfigItem[];
  audit: ConfigAuditRow[];
  lastCommand: ConfigItem | null;
  onApply: (groupId: string, key: string, value: number | string) => Promise<void>;
  canWrite?: boolean;
}

export function ConvertersPage({
  byId,
  config,
  audit,
  lastCommand,
  onApply,
  canWrite = true,
}: ConvertersPageProps) {
  const stored = config.find((item) => item.key === "cnvtr1_power_limit");
  const [limit, setLimit] = useState(stored?.value ?? 250);
  const [limit2, setLimit2] = useState(
    config.find((item) => item.key === "cnvtr2_power_limit")?.value ?? 300,
  );
  const points = [...byId.values()].filter(
    (point) => point.device_id === "cnvtr-1" || point.group_id === "cnvtr",
  );
  return (
    <section className="ems-page" aria-labelledby="cnvtr-title">
      <div className="page-title">
        <h1 id="cnvtr-title">Converters</h1>
        <p>CNVTR-1 BESS DC/DC, CNVTR-2 PV, thermal derate above 60 °C, and command outcomes.</p>
      </div>
      <div className="summary-grid">
        <article className="cds--tile">
          <span>CNVTR-1 accepted</span>
          <strong>{displayValue(byId.get("cnvtr1.test.accepted.power"))} kW</strong>
        </article>
        <article className="cds--tile">
          <span>CNVTR-1 loss</span>
          <strong>{displayValue(byId.get("cnvtr1.loss.power"))} kW</strong>
        </article>
        <article className="cds--tile">
          <span>CNVTR-1 thermal</span>
          <strong>
            {(byId.get("cnvtr1.thermal.derated")?.value ?? 0) >= 0.5 ? "derated" : "ok"} /{" "}
            {displayValue(byId.get("cnvtr1.temperature.module"))} °C
          </strong>
        </article>
        <article className="cds--tile">
          <span>CNVTR-2 accepted</span>
          <strong>{displayValue(byId.get("cnvtr2.test.accepted.power"))} kW</strong>
        </article>
      </div>
      {canWrite ? (
        <div className="ems-config-row">
          <NumberInput
            id="cnvtr1-limit"
            label="CNVTR-1 power limit (kW)"
            value={limit}
            onChange={(_event, { value }) => {
              setLimit(Number(value));
            }}
          />
          <Button
            onClick={() => {
              void onApply("cnvtr", "cnvtr1_power_limit", limit);
            }}
          >
            Apply CNVTR-1
          </Button>
          <NumberInput
            id="cnvtr2-limit"
            label="CNVTR-2 power limit (kW)"
            value={limit2}
            onChange={(_event, { value }) => {
              setLimit2(Number(value));
            }}
          />
          <Button
            onClick={() => {
              void onApply("cnvtr", "cnvtr2_power_limit", limit2);
            }}
          >
            Apply CNVTR-2
          </Button>
        </div>
      ) : null}
      <CommandOutcomeTable last={lastCommand?.group_id === "cnvtr" ? lastCommand : null} audit={audit} />
      <ConfigTable items={config} onApply={canWrite ? onApply : undefined} />
      <PointTable title="Converter points" points={points} />
    </section>
  );
}
