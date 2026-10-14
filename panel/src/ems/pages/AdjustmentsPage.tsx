import type { ConfigItem } from "../api";
import { ConfigTable } from "./shared";

const GROUPS: [string, string][] = [
  ["bms", "BESS / BMS"],
  ["cnvtr", "Converters"],
  ["solar", "Solar / MPPT"],
  ["charger", "EV Charge"],
  ["stabiliti", "Stabiliti"],
  ["supervisor", "Supervisor"],
];

interface AdjustmentsPageProps {
  configs: Record<string, ConfigItem[]>;
  onApply: (groupId: string, key: string, value: number | string) => Promise<void>;
  canWrite: boolean;
}

export function AdjustmentsPage({ configs, onApply, canWrite }: AdjustmentsPageProps) {
  return (
    <section className="ems-page" aria-labelledby="adjustments-title">
      <div className="page-title">
        <h1 id="adjustments-title">Adjustments</h1>
        <p>
          Every EMS read/write setpoint lives here. Operators change values in this UI; the
          backend stores them and applies them to the running system.
        </p>
      </div>
      {GROUPS.map(([groupId, label]) => (
        <ConfigTable
          key={groupId}
          title={label}
          items={configs[groupId] ?? []}
          onApply={canWrite ? onApply : undefined}
        />
      ))}
    </section>
  );
}
