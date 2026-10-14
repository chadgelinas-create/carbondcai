/// <reference types="vite/client" />

interface Skid2RuntimeConfigInput {
  apiBaseUrl?: unknown;
}

interface Window {
  __SKID2_CONFIG__?: Skid2RuntimeConfigInput;
}

interface ImportMetaEnv {
  readonly VITE_SKID2_SIMULATION_ENABLED?: string;
}
