import { vi } from "vitest";

import type { ApiClient } from "./client";
import { fetchCurrentState, parseCurrentState } from "./currentState";

const validResponse = {
  observed_at: "2026-07-27T10:00:00Z",
  freshness: "live",
  measurement: {
    point_id: "synthetic.walking_skeleton.test_signal_001",
    label: "Synthetic Walking-Skeleton Test Signal",
    source: "synthetic.phase2.generator",
    value: 10,
    unit: "test_unit",
    source_timestamp: "2026-07-27T10:00:00Z",
    receive_timestamp: "2026-07-27T10:00:00Z",
    quality: "good",
    synthetic: true,
  },
  heartbeat: {
    component: "synthetic.phase2.generator",
    observed_at: "2026-07-27T10:00:00Z",
    status: "healthy",
    synthetic: true,
  },
};

describe("current-state API boundary", () => {
  it("parses a complete typed response", () => {
    expect(parseCurrentState(validResponse)).toEqual(validResponse);
  });

  it.each([
    null,
    {},
    { ...validResponse, freshness: "fresh" },
    { ...validResponse, measurement: { ...validResponse.measurement, value: Number.NaN } },
    { ...validResponse, heartbeat: { ...validResponse.heartbeat, status: "unknown" } },
  ])("rejects malformed responses", (value) => {
    expect(() => parseCurrentState(value)).toThrow(/invalid response/i);
  });

  it("uses the versioned endpoint and forwards cancellation", async () => {
    const abort = new AbortController();
    const get = vi.fn<ApiClient["get"]>().mockResolvedValue(
      new Response(JSON.stringify(validResponse), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const client: ApiClient = { get, post: vi.fn(), delete: vi.fn(), put: vi.fn() };

    await expect(fetchCurrentState(client, abort.signal)).resolves.toEqual(validResponse);
    expect(get.mock.calls).toEqual([["/v1/current-state", { signal: abort.signal }]]);
  });

  it("rejects non-success HTTP responses before parsing", async () => {
    const client: ApiClient = {
      get: vi.fn().mockResolvedValue(new Response(null, { status: 503 })),
      post: vi.fn(),
      delete: vi.fn(), put: vi.fn(),
    };

    await expect(fetchCurrentState(client)).rejects.toThrow("(503)");
  });
});
