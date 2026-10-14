import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "~@ibm/plex": fileURLToPath(new URL("./node_modules/@ibm/plex", import.meta.url)),
    },
  },
  build: {
    cssMinify: false,
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    allowedHosts: ["frontend"],
    proxy: {
      "/api": {
        target: process.env.SKID2_PANEL_API_PROXY ?? "http://127.0.0.1:8000",
        changeOrigin: false,
        ws: true,
      },
    },
  },
  test: {
    include: ["src/**/*.test.{ts,tsx}"],
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    coverage: {
      provider: "v8",
      reporter: ["text", "json-summary"],
      reportsDirectory: "coverage",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/main.tsx", "src/vite-env.d.ts", "src/test/**", "src/ems/**"],
    },
  },
});
