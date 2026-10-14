export interface RuntimeConfig {
  apiBaseUrl: string;
  simulationEnabled: boolean;
}

const simulationBuildEnabled = import.meta.env.VITE_SKID2_SIMULATION_ENABLED === "true";

function readBaseUrl(value: unknown, name: string, fallback: string): string {
  if (value === undefined) {
    return fallback;
  }
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new Error(`runtime ${name} must be a non-empty string`);
  }
  return value.replace(/\/$/, "");
}

export function readRuntimeConfig(
  source: Skid2RuntimeConfigInput | undefined,
  simulationEnabled = simulationBuildEnabled,
): RuntimeConfig {
  return Object.freeze({
    apiBaseUrl: readBaseUrl(source?.apiBaseUrl, "apiBaseUrl", "/api"),
    simulationEnabled,
  });
}

export function getRuntimeConfig(): RuntimeConfig {
  return readRuntimeConfig(window.__SKID2_CONFIG__);
}
