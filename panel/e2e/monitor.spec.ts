import { expect, test, type WebSocketRoute } from "@playwright/test";

const frame = (connection: string, value: number, mode = "sim") => ({
  revision: 1,
  mode,
  connection,
  message: "Receiving saved battery readings.",
  stale_seconds: 30,
  state: {
    observed_at: new Date().toISOString(),
    freshness: "live",
    heartbeat: null,
    measurement: {
      point_id: "bess.cluster.soc",
      label: "Battery charge",
      source: `${mode}:ba1-1:revision:1`,
      value,
      unit: "ratio",
      quality: "good",
      synthetic: mode === "sim",
      source_timestamp: new Date().toISOString(),
      receive_timestamp: new Date().toISOString(),
    },
  },
});

for (const dark of [false, true]) {
  test(`battery monitoring keeps source and connection clear in ${dark ? "dark" : "light"} theme`, async ({
    page,
  }) => {
    await page.route("**/api/v1/session", (route) =>
      route.fulfill({ json: { username: "viewer", role: "viewer" } }),
    );
    let socket: WebSocketRoute | undefined;
    await page.routeWebSocket("**/api/v1/monitor/stream", (route) => {
      socket = route;
      route.send(JSON.stringify(frame("connected", 0.5)));
    });
    await page.goto("/");
    if (dark) {
      await page.locator('label[for="theme"]').click();
      await expect(page.getByRole("switch", { name: "Dark theme" })).toBeChecked();
    }
    await expect(page.getByRole("heading", { name: "Battery monitoring" })).toBeVisible();
    await expect(page.getByText("50.0%", { exact: true })).toBeVisible();
    await expect(page.getByText("Data source: Simulation (SIM)")).toBeVisible();
    await expect(page.getByRole("button", { name: "Simulator Lab", exact: true })).toHaveCount(0);
    socket?.send(JSON.stringify(frame("disconnected", 0.5)));
    await expect(page.getByText("Disconnected", { exact: true })).toBeVisible();
    await expect(page.getByText("0%", { exact: true })).toBeVisible();
    socket?.send(JSON.stringify(frame("connected", 0.7, "live")));
    await expect(page.getByText("70.0%", { exact: true })).toBeVisible();
    await expect(page.getByText("Data source: Live equipment (LIVE)")).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
  });
}

test("login screen hides operational controls", async ({ page }) => {
  await page.route("**/api/v1/session", (route) =>
    route.fulfill({ status: 401, json: { detail: "Please sign in" } }),
  );
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect(page.getByLabel("Username")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save settings" })).toHaveCount(0);
  await page.getByLabel("Username").focus();
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Password", { exact: true })).toBeFocused();
});
