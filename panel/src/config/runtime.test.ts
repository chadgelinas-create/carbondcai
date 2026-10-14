import { readRuntimeConfig } from "./runtime";

describe("readRuntimeConfig", () => {
  it("uses the same-origin API boundary by default", () => {
    expect(readRuntimeConfig(undefined, false)).toEqual({
      apiBaseUrl: "/api",
      simulationEnabled: false,
    });
  });

  it("normalizes a configured trailing slash", () => {
    expect(readRuntimeConfig({ apiBaseUrl: "https://example.test/api/" }, false)).toEqual({
      apiBaseUrl: "https://example.test/api",
      simulationEnabled: false,
    });
  });

  it("rejects malformed browser configuration", () => {
    expect(() => readRuntimeConfig({ apiBaseUrl: 42 }, false)).toThrow(/non-empty string/);
    expect(() => readRuntimeConfig({ apiBaseUrl: "" }, false)).toThrow(/non-empty string/);
  });

  it("uses the same API when the build enables simulation", () => {
    expect(readRuntimeConfig(undefined, true)).toEqual({
      apiBaseUrl: "/api",
      simulationEnabled: true,
    });
  });
});
