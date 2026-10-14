import fixture from "../src/test/simulation.json" with { type: "json" };
import calculated from "../src/test/bus.json" with { type: "json" };
import { parseSimulationState } from "../src/api/simulation";
import { expect, test, type Page } from "@playwright/test";

import type { SimulationDevice, SimulationInputs, SimulationState } from "../src/api/simulation";

// Existing simulation tests use a signed-in operator.
test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/session", (route) =>
    route.fulfill({
      json: { username: "test-operator", role: "operator", csrf_token: "test-csrf" },
    }),
  );
});

function inputs(deviceId: string): SimulationInputs {
  if (deviceId === "ba1-1") {
    return {
      kind: "battery",
      cluster_soc: 0.5,
      voltage: 0,
      current: 0,
      maximum_temperature: 25,
      charge_power_limit: 0,
      discharge_power_limit: 0,
      main_contactor: false,
      precharge_contactor: false,
      precharge_state: 0,
      fire_communication_state: 0,
      communication_ok: true,
      fault_active: false,
    };
  }
  if (deviceId.startsWith("cnvtr-")) {
    return {
      kind: "converter",
      enabled: false,
      requested_power: 0,
      available_power: 0,
      charge_power_limit: 0,
      discharge_power_limit: 0,
      dc_voltage: 0,
      dc_current: 0,
      temperature: 25,
      fault_active: false,
      communication_ok: true,
      pv_available: false,
      mppt_applying: false,
      test_ramp_per_step: null,
      test_derate_limit: null,
    };
  }
  if (deviceId.startsWith("st-")) {
    return {
      kind: "stabiliti",
      bus_load: null,
      enabled: true,
      available_power: 0,
      actual_power: 0,
      power_limit: 0,
      maximum_temperature: 25,
      derated: false,
      communication_ok: true,
      fault_active: false,
    };
  }
  return {
    kind: "dc600",
    demand_power: null,
    available_power: null,
    actual_power: null,
    demand_fresh: true,
    available_fresh: true,
    session_active: false,
    communication_ok: true,
    fault_active: false,
  };
}

function device(deviceId: string, displayName: string): SimulationDevice {
  const mapped = !deviceId.startsWith("st-") && deviceId !== "dc600";
  return {
    device_id: deviceId,
    display_name: displayName,
    role: "Test device",
    lifecycle: "ready",
    health: "stopped",
    test_id: null,
    test_started_at: null,
    implementation_status: "simulated",
    protocol_status: mapped ? "candidate" : "blocked",
    protocol: mapped ? "Modbus TCP" : "Physical interface not supplied",
    endpoint_status: mapped ? "candidate" : "unknown",
    model_status: "unknown",
    firmware_status: "unknown",
    blocker_reason: mapped ? null : "Physical equipment interface is not supplied.",
    capabilities: [],
    values: [],
    faults: [],
    inputs: inputs(deviceId),
    applied_inputs: inputs(deviceId),
    inputs_pending: false,
    status_text: "Ready",
    testing_method: "Simulator Lab",
    direct_connection: mapped ? "Modbus TCP simulator" : "Built-in simulator",
    test_notice: mapped ? null : "Software simulation is ready.",
  };
}

const simulationState: SimulationState = {
  battery_history: [],
  bus_history: [],
  pv_history: [],
  plant: parseSimulationState(fixture).plant,
  plant_trace_hash: fixture.plant_trace_hash,
  failure: null,
  snapshot_saved: false,
  saved_run: null,
  restored_run: null,
  lifecycle: "stopped",
  at: "2026-08-01T10:00:00Z",
  sequence: 0,
  trace_entries: 0,
  trace_hash: "0".repeat(64),
  devices: [
    device("ba1-1", "BA1-1 BESS/BMS"),
    device("cnvtr-1", "CNVTR-1"),
    device("cnvtr-2", "CNVTR-2"),
    device("dc600", "DC600 charge controller"),
    device("st-4", "Stabiliti 01"),
    device("st-5", "Stabiliti 02"),
    device("st-6", "Stabiliti 03"),
  ],
  activity: [],
  stabiliti_fleet: {
    ready: 0,
    available: 0,
    derated: 0,
    faulted: 0,
    total: 3,
    available_power: 0,
  },
  gateway: {
    state: "ready",
    host: "127.0.0.1",
    port: 1502,
    clients: 0,
    last_request: null,
    routes: [
      { device_id: "ba1-1", modbus_id: 1, functions: [4], registers: [] },
      { device_id: "cnvtr-1", modbus_id: 2, functions: [3], registers: [] },
      { device_id: "cnvtr-2", modbus_id: 3, functions: [3], registers: [] },
    ],
  },
  fault_rules: [
    {
      fault_type: "communication_loss",
      label: "Communication loss",
      device_ids: ["ba1-1", "cnvtr-1", "cnvtr-2", "dc600", "st-4", "st-5", "st-6"],
      needs_point: false,
      needs_value: false,
      latched: false,
      expected_result:
        "The device becomes unavailable and its values show a communication problem.",
      clear_method: "Clears after its duration or when cleared manually.",
      fidelity: "Logical software simulation",
      evidence: "Project simulator behaviour",
    },
  ],
  fault_tests: [],
};

async function openLab(page: Page): Promise<void> {
  await page.route("**/api/v1/simulation**", async (route) => {
    await route.fulfill({ json: simulationState });
  });
  await page.goto("/simulator");
  await expect(page.getByRole("button", { name: "Devices" })).toBeVisible();
}

async function checkLayout(page: Page): Promise<void> {
  await expect(page.getByRole("navigation", { name: "Workspace", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Account", exact: true })).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
    .toBe(true);
}

test("shows seven devices within the page layout", async ({ page }) => {
  await openLab(page);

  await expect(page.getByRole("heading", { name: "Simulator Lab" })).toBeVisible();
  await page.getByRole("button", { name: "Devices" }).click();
  await expect(page.getByRole("row")).toHaveCount(8);
  await expect(page.getByText("Ready for Modbus testing")).toHaveCount(3);
  await expect(page.getByText("Built-in simulator")).toHaveCount(4);
  await expect(page.getByText("Stabiliti 01")).toBeVisible();
  await expect(page.getByText("Stabiliti 02")).toBeVisible();
  await expect(page.getByText("Stabiliti 03")).toBeVisible();
  await checkLayout(page);
});

test("supports keyboard access and narrow reflow", async ({ page }) => {
  await openLab(page);

  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#main-content$/);

  await page.setViewportSize({ width: 320, height: 800 });
  await expect(page.getByRole("heading", { name: "Simulator Lab" })).toBeVisible();
  const pageWidth = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  expect(pageWidth.scroll).toBeLessThanOrEqual(pageWidth.client);
});

test("starts and clears a guided fault test without internal ticket wording", async ({ page }) => {
  let current = simulationState;
  await page.route("**/api/v1/simulation**", async (route) => {
    const request = route.request();
    if (request.method() === "POST" && request.url().endsWith("/faults")) {
      current = {
        ...current,
        fault_tests: [
          {
            test_id: "test-0001",
            device_id: "ba1-1",
            fault_type: "communication_loss",
            label: "Communication loss",
            point_id: null,
            test_value: null,
            start_step: 1,
            duration_steps: null,
            started_at: "2026-08-01T10:00:01Z",
            cleared_at: null,
            state: "scheduled",
            latched: false,
            expected_result:
              "The device becomes unavailable and its values show a communication problem.",
            clear_method: "Clears after its duration or when cleared manually.",
          },
        ],
      };
    } else if (request.method() === "POST" && request.url().endsWith("/clear")) {
      current = {
        ...current,
        fault_tests: current.fault_tests.map((item) => ({
          ...item,
          state: "cleared" as const,
          cleared_at: "2026-08-01T10:00:01Z",
        })),
      };
    }
    await route.fulfill({ json: current });
  });
  await page.goto("/simulator");

  await page.getByRole("button", { name: "Fault testing" }).click();
  await page.getByLabel("Problem to test").selectOption({ label: "Communication loss" });
  await expect(page.getByText(/device becomes unavailable/i)).toBeVisible();
  await page.getByRole("button", { name: "Start test" }).click();
  await expect(page.getByText("test-0001")).toBeVisible();
  await expect(page.getByText("Scheduled", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Clear test" }).click();
  await expect(page.getByText("Cleared", { exact: true })).toBeVisible();
  await expect(page.getByText(/F08|F09|Phase 4/i)).toHaveCount(0);
});

for (const theme of ["light", "dark"] as const) {
  test(`shows waiting power inputs in the ${theme} theme`, async ({ page }) => {
    await openLab(page);
    await page.getByRole("button", { name: "Plant Lab", exact: true }).focus();
    await page.keyboard.press("Enter");
    if (theme === "dark") {
      await page.locator('label[for="theme"]').click();
      await expect(page.getByRole("switch", { name: "Dark theme" })).toBeChecked();
    }
    await expect(page.getByRole("heading", { name: "Plant Lab", exact: true })).toBeVisible();
    await expect(page.getByRole("combobox", { name: "Profile" })).toHaveValue("stage0");
    await expect(
      page.getByText(/\bstage[ -]?0\b|\bphase \d+\b|\bP\d+-F\d+\b|\bplant\.[a-z.]+\b/i),
    ).toHaveCount(0);
    await expect(page.getByText("Available in this version", { exact: true })).toBeVisible();
    await expect(page.getByText("Not included in this version.").first()).toBeVisible();
    await expect(
      page.getByText(/None enabled|None recorded|Not set|Not tested|SRC-002|SRC-006/),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Restore saved run", exact: true }),
    ).toBeDisabled();
    await page.getByRole("spinbutton", { name: "Repeat number" }).focus();
    await page.keyboard.press("Tab");
    await expect(page.locator(":focus")).toBeVisible();
    await page.evaluate(() => {
      window.scrollTo(0, 0);
    });
    await checkLayout(page);
  });

  test(`shows calculated bus power in the ${theme} theme`, async ({ page }) => {
    test.setTimeout(60000);
    await page.route("**/api/v1/simulation**", (route) => route.fulfill({ json: calculated }));
    await page.goto("/simulator");
    await page.getByRole("button", { name: "Plant Lab", exact: true }).click();
    if (theme === "dark") await page.locator('label[for="theme"]').click();
    await expect(
      page.getByRole("img", { name: /DC bus voltage over simulation time/ }),
    ).toBeVisible();
    await expect(page.getByText("Power by device", { exact: true })).toBeVisible();
    await page.getByText("View voltage readings", { exact: true }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("cell", { name: "Calculated", exact: true })).toHaveCount(3);
    await expect(
      page.getByText(/\bstage[ -]?0\b|\bphase \d+\b|\bP\d+-F\d+\b|\bplant\.[a-z.]+\b/i),
    ).toHaveCount(0);
    await page.evaluate(() => {
      window.scrollTo(0, 0);
    });
    await checkLayout(page);
    await page.setViewportSize({ width: 320, height: 800 });
    await expect.poll(() => page.evaluate(() => document.documentElement.clientWidth)).toBe(320);
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth), { timeout: 20000 })
      .toBeLessThanOrEqual(320);
  });
}

test("keeps unsaved bus demand while refreshing device values", async ({ page }) => {
  await page.clock.install();
  let payload: unknown;
  await page.route("**/api/v1/simulation**", async (route) => {
    if (route.request().method() === "POST") payload = route.request().postDataJSON();
    await route.fulfill({ json: calculated });
  });
  await page.goto("/simulator");
  await page.getByRole("button", { name: "Devices", exact: true }).click();
  await page.getByRole("row").filter({ hasText: "Stabiliti 01" }).getByRole("button").click();
  const input = page.getByRole("spinbutton", { name: "Power needed from DC bus (kW)" });
  await input.fill("7");
  await page.clock.runFor(2100);
  await expect(input).toHaveValue("7");
  await page.getByRole("button", { name: "Apply values", exact: true }).click();
  expect(payload).toMatchObject({ kind: "stabiliti", bus_load: 7, actual_power: 0 });
});

test("shows frame failure and reconnects without losing the last plant frame", async ({ page }) => {
  await page.clock.install();
  let offline = false;
  let failed = true;
  await page.route("**/api/v1/simulation**", async (route) => {
    if (offline) return route.abort();
    await route.fulfill({
      json: {
        ...simulationState,
        failure: failed
          ? {
              at: simulationState.at,
              sequence: 1,
              code: "invalid_plant_step",
            }
          : null,
      },
    });
  });
  await page.goto("/simulator");
  await page.getByRole("button", { name: "Plant Lab", exact: true }).click();
  await expect(page.getByText("Simulation paused after a problem", { exact: true })).toBeVisible();
  offline = true;
  await page.clock.runFor(1100);
  await expect(page.getByText("Simulator offline", { exact: true })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Profile" })).toHaveValue("stage0");
  await expect(
    page.getByText(/\bstage[ -]?0\b|\bphase \d+\b|\bP\d+-F\d+\b|\bplant\.[a-z.]+\b/i),
  ).toHaveCount(0);
  failed = false;
  offline = false;
  await page.clock.runFor(1100);
  await expect(
    page.getByText("Connection restored. Current simulation state is shown."),
  ).toBeVisible();
  await expect(page.getByText("Simulation paused after a problem", { exact: true })).toHaveCount(0);
});

test("runs snapshots and replay against the live shared runtime", async ({ page }) => {
  test.setTimeout(60000);
  test.skip(
    process.env.SKID2_LIVE_API !== "true",
    "Requires the isolated Docker acceptance backend",
  );
  const initial = parseSimulationState(
    await (await page.request.post("/api/v1/simulation/reset")).json(),
  );
  for (const device of initial.devices) {
    let next: SimulationInputs | undefined;
    if (device.inputs.kind === "stabiliti") next = { ...device.inputs, bus_load: 0 };
    if (device.inputs.kind === "dc600")
      next = { ...device.inputs, demand_power: 0, available_power: 100 };
    if (device.device_id === "cnvtr-2" && device.inputs.kind === "converter")
      next = {
        ...device.inputs,
        enabled: true,
        pv_available: true,
        requested_power: 10,
        available_power: 100,
      };
    if (next) {
      const response = await page.request.post(
        `/api/v1/simulation/devices/${device.device_id}/inputs`,
        { data: next },
      );
      expect(response.ok()).toBe(true);
    }
  }
  await page.goto("/simulator");
  await page.getByRole("button", { name: "Plant Lab", exact: true }).click();
  await page.getByRole("button", { name: "Next step", exact: true }).click();
  await expect(page.getByText("One step complete.")).toBeVisible();
  await page.getByRole("button", { name: "Save run", exact: true }).click();
  await expect(page.getByText("Saved for this session", { exact: true })).toBeVisible();
  const saved = parseSimulationState(await (await page.request.get("/api/v1/simulation")).json());
  expect(saved.plant.balance).toBe("available");
  expect(saved.plant.bus.find((reading) => reading.name === "Bus voltage")?.value).toBeCloseTo(
    763.216876,
    5,
  );
  await expect(
    page.getByRole("img", { name: /DC bus voltage over simulation time/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Next step", exact: true }).click();
  await expect(page.getByText("One step complete.")).toBeVisible();
  await page.getByRole("button", { name: "Restore saved run", exact: true }).click();
  await expect(page.getByText("Saved run restored. Paused.")).toBeVisible();
  await page.getByRole("button", { name: "Repeat steps", exact: true }).click();
  await expect(page.getByText("Steps repeated.")).toBeVisible();
  const replayed = parseSimulationState(
    await (await page.request.get("/api/v1/simulation")).json(),
  );
  expect(replayed.plant_trace_hash).toBe(saved.plant_trace_hash);
  expect(replayed.trace_hash).toBe(saved.trace_hash);
  expect(replayed.plant).toEqual(saved.plant);
  expect(replayed.bus_history).toEqual(saved.bus_history);
  const second = await page.context().newPage();
  await second.goto("/simulator");
  await second.getByRole("button", { name: "Plant Lab", exact: true }).click();
  const browserSteps: string[] = [];
  page.context().on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/step"))
      browserSteps.push(request.url());
  });
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect
    .poll(
      async () =>
        parseSimulationState(await (await page.request.get("/api/v1/simulation")).json()).sequence,
      { timeout: 15000, intervals: [250] },
    )
    .toBeGreaterThan(saved.sequence + 1);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(page.getByText("Paused.", { exact: true })).toBeVisible();
  expect(browserSteps).toEqual([]);
  await second.close();
});

test("runs solar profiles and restore against the live shared runtime", async ({ page }) => {
  test.setTimeout(60000);
  test.skip(
    process.env.SKID2_LIVE_API !== "true",
    "Requires the isolated Docker acceptance backend",
  );
  await page.goto("/simulator");
  await page.getByRole("button", { name: "Plant Lab", exact: true }).click();
  await page.getByRole("combobox", { name: "Profile" }).selectOption("solar_cloud");
  await page.getByRole("button", { name: "Apply and reset" }).click();
  await expect(page.getByText("Solar power by device", { exact: true })).toBeVisible();
  const configured = parseSimulationState(
    await (await page.request.get("/api/v1/simulation")).json(),
  );
  const demands: Record<string, number> = { "st-4": 10, "st-5": 15, "st-6": 20 };
  for (const device of configured.devices) {
    const next =
      device.inputs.kind === "stabiliti"
        ? { ...device.inputs, bus_load: demands[device.device_id] }
        : device.inputs.kind === "dc600"
          ? { ...device.inputs, demand_power: 20 }
          : undefined;
    if (next) {
      const response = await page.request.post(
        `/api/v1/simulation/devices/${device.device_id}/inputs`,
        { data: next },
      );
      expect(response.ok()).toBe(true);
    }
  }
  await page.getByRole("button", { name: "Next step", exact: true }).click();
  await expect(page.getByText("One step complete.")).toBeVisible();
  await page.getByRole("button", { name: "Save run", exact: true }).click();
  await expect(page.getByText("Saved for this session", { exact: true })).toBeVisible();
  const saved = parseSimulationState(await (await page.request.get("/api/v1/simulation")).json());
  expect(saved.plant.pv_paths).toHaveLength(1);
  expect(saved.plant.balance).toBe("available");
  expect(saved.plant.ledger.find((value) => value.name === "Power used")?.value).toBe(65);
  expect(saved.plant.ledger.find((value) => value.name === "Stored power")?.value).toBeCloseTo(30);
  for (const port of saved.plant.ports.filter((port) => port.device_id.startsWith("st-"))) {
    expect(port.outputs.find((value) => value.name === "AC power supplied")?.value).toBe(
      demands[port.device_id],
    );
  }
  await expect(
    page.getByRole("img", { name: /Power delivered over simulation time/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Next step", exact: true }).click();
  await page.getByRole("button", { name: "Restore saved run", exact: true }).click();
  await expect(page.getByText("Saved run restored. Paused.")).toBeVisible();
  await page.getByRole("button", { name: "Repeat steps", exact: true }).click();
  await expect(page.getByText("Steps repeated.")).toBeVisible();
  const replayed = parseSimulationState(
    await (await page.request.get("/api/v1/simulation")).json(),
  );
  expect(replayed.plant_trace_hash).toBe(saved.plant_trace_hash);
  expect(replayed.plant.pv_paths).toEqual(saved.plant.pv_paths);
  expect(replayed.pv_history).toEqual(saved.pv_history);
  await page.getByRole("combobox", { name: "Profile" }).selectOption("stage0");
  await page.getByRole("button", { name: "Apply and reset" }).click();
  await expect(page.getByText("Solar power by device", { exact: true })).toHaveCount(0);
});

for (const theme of ["light", "dark"]) {
  test(`shows bus-connected solar in the ${theme} theme`, async ({ page }) => {
    const { default: solar } = await import("../src/test/solar.json", { with: { type: "json" } });
    await page.route("**/api/v1/simulation**", (route) => route.fulfill({ json: solar }));
    await page.goto("/simulator");
    await page.getByRole("button", { name: "Plant Lab", exact: true }).click();
    if (theme === "dark") await page.locator('label[for="theme"]').click();
    await expect(page.getByRole("combobox", { name: "Profile" })).toHaveValue("solar_cloud");
    await expect(page.getByText("Solar power by device", { exact: true })).toBeVisible();
    await expect(
      page.getByRole("img", { name: /Power delivered over simulation time/ }),
    ).toBeVisible();
    await expect(
      page.getByText(/\bstage[ -]?0\b|\bphase \d+\b|\bP\d+-F\d+\b|\bplant\.[a-z.]+\b/i),
    ).toHaveCount(0);
    await expect(page.getByRole("combobox", { name: "Solar source" })).toHaveCount(0);
    await page.getByRole("combobox", { name: "Solar measurement" }).selectOption("Unused solar");
    await page.getByText("View solar readings", { exact: true }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("columnheader", { name: "Unused solar (kW)" })).toBeVisible();
    await page.evaluate(() => {
      window.scrollTo(0, 0);
    });
    await checkLayout(page);
    await page.getByText("Solar settings and assumptions", { exact: true }).click();
    await page.setViewportSize({ width: 320, height: 800 });
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth), { timeout: 20000 })
      .toBeLessThanOrEqual(320);
  });
}

for (const theme of ["light", "dark"]) {
  test(`shows battery storage in the ${theme} theme`, async ({ page }) => {
    const { default: battery } = await import("../src/test/battery.json", {
      with: { type: "json" },
    });
    await page.route("**/api/v1/simulation**", (route) => route.fulfill({ json: battery }));
    await page.goto("/simulator");
    await page.getByRole("button", { name: "Plant Lab", exact: true }).click();
    if (theme === "dark") await page.locator('label[for="theme"]').click();
    await expect(page.getByRole("combobox", { name: "Battery setup" })).toHaveValue("battery_3500");
    await expect(page.getByText("Direction: Charging")).toBeVisible();
    await expect(
      page.getByRole("img", { name: /Battery level over simulation time/ }),
    ).toBeVisible();
    await expect(
      page.getByText(/\bstage[ -]?0\b|\bphase \d+\b|\bP\d+-F\d+\b|\bplant\.[a-z.]+\b/i),
    ).toHaveCount(0);
    await page.getByRole("combobox", { name: "Battery measurement" }).selectOption("Actual power");
    await page.getByText("View battery readings", { exact: true }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("columnheader", { name: "Actual power (kW)" })).toBeVisible();
    await page.evaluate(() => {
      window.scrollTo(0, 0);
    });
    await checkLayout(page);
    await page.getByText("Battery settings and assumptions", { exact: true }).click();
    await page.setViewportSize({ width: 320, height: 800 });
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth), { timeout: 20000 })
      .toBeLessThanOrEqual(320);
  });
}

test("runs battery charging, fault recovery and replay against the live runtime", async ({
  page,
}) => {
  test.setTimeout(60000);
  test.skip(
    process.env.SKID2_LIVE_API !== "true",
    "Requires the isolated Docker acceptance backend",
  );
  await page.request.post("/api/v1/simulation/pause");
  await page.request.post("/api/v1/simulation/profile", {
    data: { profile_id: "solar_cloud", seed: 0 },
  });
  await page.goto("/simulator");
  await page.getByRole("button", { name: "Plant Lab", exact: true }).click();
  await page.getByRole("combobox", { name: "Battery setup" }).selectOption("battery_3500");
  await page.getByRole("button", { name: "Apply and reset" }).click();
  await expect(page.getByRole("region", { name: "Battery storage details" })).toBeVisible();
  const initial = parseSimulationState(await (await page.request.get("/api/v1/simulation")).json());
  for (const device of initial.devices) {
    if (device.device_id === "cnvtr-1" && device.inputs.kind === "converter") {
      await page.request.post(`/api/v1/simulation/devices/${device.device_id}/inputs`, {
        data: { ...device.inputs, requested_power: -40 },
      });
    }
    if (device.inputs.kind === "dc600") {
      await page.request.post(`/api/v1/simulation/devices/${device.device_id}/inputs`, {
        data: { ...device.inputs, demand_power: 0 },
      });
    }
  }
  await page.getByRole("button", { name: "Next step" }).click();
  await expect(page.getByText("Direction: Charging")).toBeVisible();
  const charged = parseSimulationState(await (await page.request.get("/api/v1/simulation")).json());
  expect(charged.plant.bess[0]?.value).toBeGreaterThan(1750);
  await page.request.post("/api/v1/simulation/faults", {
    data: { device_id: "ba1-1", fault_type: "communication_loss", duration_steps: 1 },
  });
  const held = parseSimulationState(
    await (await page.request.post("/api/v1/simulation/step")).json(),
  );
  expect(held.plant.bess[0]?.quality).toBe("stale");
  expect(held.plant.bess[0]?.value).toBe(charged.plant.bess[0]?.value);
  const recovered = parseSimulationState(
    await (await page.request.post("/api/v1/simulation/step")).json(),
  );
  expect(recovered.failure).toBeNull();
  expect(recovered.plant.balance).toBe("available");
  const repeated = parseSimulationState(
    await (await page.request.post("/api/v1/simulation/replay")).json(),
  );
  expect(repeated.plant_trace_hash).toBe(recovered.plant_trace_hash);
  await page.request.post("/api/v1/simulation/profile", {
    data: { profile_id: "stage0", seed: 0 },
  });
});
