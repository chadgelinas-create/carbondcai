import { vi } from "vitest";

import { createApiClient } from "./client";
import { fetchCurrentState } from "./currentState";

describe("createApiClient", () => {
  it("keeps endpoint construction behind one boundary", async () => {
    const response = new Response(null, { status: 204 });
    const fetchImplementation = vi.fn<typeof fetch>().mockResolvedValue(response);
    const client = createApiClient({ apiBaseUrl: "/api" }, fetchImplementation);

    await expect(
      client.get("/v1/health/components", { headers: { Accept: "application/json" } }),
    ).resolves.toBe(response);
    expect(fetchImplementation).toHaveBeenCalledWith("/api/v1/health/components", {
      headers: new Headers({ Accept: "application/json" }),
      credentials: "same-origin",
      method: "GET",
    });
  });

  it("rejects non-absolute API paths", () => {
    const client = createApiClient({ apiBaseUrl: "/api" }, vi.fn<typeof fetch>());

    expect(() => client.get("health")).toThrow(/begin with/);
    expect(() => client.post("start")).toThrow(/begin with/);
  });

  it("uses an explicit POST boundary", async () => {
    const fetchImplementation = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(null, { status: 204 }));
    const client = createApiClient({ apiBaseUrl: "/api" }, fetchImplementation);

    await client.post("/v1/simulation/step");

    expect(fetchImplementation).toHaveBeenCalledWith("/api/v1/simulation/step", {
      method: "POST",
      headers: new Headers(),
      credentials: "same-origin",
    });
  });

  it("constructs the real current-state route with one API prefix", async () => {
    const fetchImplementation = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          observed_at: "2026-07-27T10:00:00Z",
          freshness: "unavailable",
          measurement: null,
          heartbeat: null,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    const client = createApiClient({ apiBaseUrl: "/api" }, fetchImplementation);

    await fetchCurrentState(client);

    expect(fetchImplementation.mock.calls[0]?.[0]).toBe("/api/v1/current-state");
  });
});

it("sends CSRF verification with checkpoint deletion", async () => {
  document.cookie = "skid2_csrf=delete-test-token";
  const fetchImplementation = vi
    .fn<typeof fetch>()
    .mockResolvedValue(new Response(null, { status: 204 }));
  const client = createApiClient({ apiBaseUrl: "/api" }, fetchImplementation);
  await client.delete("/v1/simulation/snapshot");
  expect(fetchImplementation).toHaveBeenCalledWith("/api/v1/simulation/snapshot", {
    method: "DELETE",
    headers: new Headers({ "X-CSRF-Token": "delete-test-token" }),
    credentials: "same-origin",
  });
  document.cookie = "skid2_csrf=; Max-Age=0";
});
