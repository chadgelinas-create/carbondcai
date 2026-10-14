import { expect, test } from "@playwright/test";

test("login, account controls and theme remain consistent through refresh and sign-out", async ({
  page,
}) => {
  let signedIn = false;
  const user = { username: "admin", role: "admin" };
  await page.route("**/api/v1/session", (route) =>
    route.fulfill({ status: signedIn ? 200 : 401, json: signedIn ? user : {} }),
  );
  await page.route("**/api/v1/login", (route) => {
    const credentials = route.request().postDataJSON() as { password: string };
    signedIn = credentials.password === "correct-password";
    return route.fulfill({ status: signedIn ? 200 : 401, json: signedIn ? user : {} });
  });
  await page.route("**/api/v1/logout", (route) => {
    signedIn = false;
    return route.fulfill({ status: 204 });
  });
  await page.route("**/api/v1/simulation", (route) => route.fulfill({ status: 503, json: {} }));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect(page.getByRole("banner")).toHaveCount(0);
  await page.locator('label[for="theme"]').click();
  await page.reload();
  await expect(page.getByRole("switch", { name: "Dark theme" })).toBeChecked();
  await page.getByLabel("Username").fill("admin");
  await page.getByLabel("Password", { exact: true }).fill("wrong-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByText(/Check your username and password/)).toBeVisible();
  await page.getByLabel("Password", { exact: true }).fill("correct-password");
  await page.getByLabel("Password", { exact: true }).press("Enter");
  const account = page.getByRole("button", { name: "Account", exact: true });
  await expect(account).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign out" })).toHaveCount(0);
  await expect(page.getByText("admin", { exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("switch", { name: "Dark theme" })).toBeChecked();
  await account.click();
  await expect(page.getByText("Administrator", { exact: true })).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Sign out" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(account).toBeFocused();
  await expect(account).toHaveAttribute("aria-expanded", "false");
  await account.click();
  await page.getByRole("navigation", { name: "Workspace" }).click();
  await expect(account).toHaveAttribute("aria-expanded", "false");
  await account.click();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect(page.getByRole("banner")).toHaveCount(0);
  await expect(page.getByRole("switch", { name: "Dark theme" })).toBeChecked();
  await expect(page.getByLabel("Password", { exact: true })).toBeEmpty();
});

test("login fits a phone and respects the saved preference over the system theme", async ({
  page,
}) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.route("**/api/v1/session", (route) => route.fulfill({ status: 401, json: {} }));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect(page.getByRole("switch", { name: "Dark theme" })).toBeChecked();
  await page.locator('label[for="theme"]').click();
  await page.reload();
  await expect(page.getByRole("switch", { name: "Dark theme" })).not.toBeChecked();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  expect(
    await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight),
  ).toBe(true);
});
