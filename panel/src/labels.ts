export function title(value: string): string {
  return value
    .split("_")
    .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

const valueLabels: Readonly<Record<string, string>> = {
  "st-4.actual.power": "Reported AC power",
  "st-5.actual.power": "Reported AC power",
  "st-6.actual.power": "Reported AC power",
  "st-4.bus.load": "Power needed from DC bus",
  "st-5.bus.load": "Power needed from DC bus",
  "st-6.bus.load": "Power needed from DC bus",
  "bess.cluster.limit.charge.power": "Charge power limit",
  "bess.cluster.limit.discharge.power": "Discharge power limit",
  "bess.fire.communication.state": "Fire communication state",
  "dc600.handshake.live": "Handshake",
  "dc600.session.active": "Charging session",
  "dc600.status": "Status",
  "dc600.request.limited": "Request limited",
  "dc600.derate.reason": "Reason",
  "dc600.communication.ok": "Communication",
  "dc600.fault.active": "Test fault",
  "dc600.demand.power": "Requested power",
  "dc600.demand.updated.at": "Demand time",
  "dc600.available.power": "Available power",
  "dc600.available.updated.at": "Available-power time",
  "dc600.accepted.power": "Accepted power",
  "dc600.actual.power": "Actual charging load",
};

const valueSuffixLabels: readonly (readonly [string, string])[] = [
  [".temperature.maximum", "Maximum temperature"],
  [".communication.ok", "Communication"],
  [".fault.active", "Test fault"],
  [".available.power", "Available power"],
  [".actual.power", "Actual power"],
  [".power.limit", "Power limit"],
  [".available", "Available"],
  [".derated", "Derated"],
  [".enabled", "Enabled"],
  [".ready", "Ready"],
];

export function valueLabel(logicalId: string): string {
  const exact = valueLabels[logicalId];
  if (exact !== undefined) return exact;
  const suffix = valueSuffixLabels.find(([ending]) => logicalId.endsWith(ending));
  return suffix?.[1] ?? title(logicalId.split(".").slice(-2).join(" "));
}
