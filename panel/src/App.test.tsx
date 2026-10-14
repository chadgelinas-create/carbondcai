import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";

import { App } from "./App";
import { PlantLab } from "./PlantLab";
import { parseSimulationState } from "./api/simulation";
import battery from "./test/battery.json";
import type { ApiClient } from "./api/client";

const defaultBus = {
  nominal_voltage_v: 750,
  max_voltage_v: 1500,
  capacitance_f: 1,
  loss_percent: 0,
  tolerance_kw: 0.000001,
};
const customBus = {
  nominal_voltage_v: 800,
  max_voltage_v: 900,
  capacitance_f: 2,
  loss_percent: 10,
  tolerance_kw: 0.00001,
};

function waitingClient(role: "viewer" | "operator" | "admin" = "admin"): ApiClient {
  return {
    get: vi.fn((path: string) => {
      if (path === "/v1/session") {
        return Promise.resolve(Response.json({ username: role, role }));
      }
      if (path === "/v1/auth/from-lab") {
        return Promise.resolve(Response.json({ token: "ems", username: role, role }));
      }
      return new Promise<Response>(() => undefined);
    }),
    post: vi.fn(),
    delete: vi.fn(), put: vi.fn(),
  };
}

describe("App", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    localStorage.clear();
    sessionStorage.clear();
  });

  it("keeps the workspace header hidden while checking a session and on the login screen", async () => {
    const client = { get: vi.fn(), post: vi.fn(), delete: vi.fn(), put: vi.fn() } satisfies ApiClient;
    let resolveSession!: (response: Response) => void;
    client.get.mockReturnValue(
      new Promise<Response>((resolve) => {
        resolveSession = resolve;
      }),
    );
    render(<App client={client} />);
    expect(screen.getByText("Checking sign-in…")).toBeVisible();
    expect(screen.queryByRole("banner")).not.toBeInTheDocument();
    resolveSession(new Response(null, { status: 401 }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeVisible();
    expect(screen.getByRole("button", { name: /User login/ })).toBeVisible();
    expect(screen.getByRole("button", { name: /Admin login/ })).toBeVisible();
    expect(screen.queryByRole("banner")).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Simulator Lab" })).not.toBeInTheDocument();
  });

  it("opens user and admin login forms from the wireframe without showing the app", async () => {
    const client = {
      get: vi.fn().mockResolvedValue(new Response(null, { status: 401 })),
      post: vi.fn(),
      delete: vi.fn(),
      put: vi.fn(),
    } satisfies ApiClient;
    render(<App client={client} />);
    fireEvent.click(await screen.findByRole("button", { name: /User login/ }));
    expect(screen.getByRole("button", { name: /User login/ })).toBeVisible();
    expect(screen.getByRole("button", { name: /Admin login/ })).toBeVisible();
    expect(screen.getByRole("button", { name: "Sign in as user" })).toBeVisible();
    expect(screen.getByLabelText("Username")).toHaveValue("");
    expect(screen.queryByRole("banner")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Admin login/ }));
    expect(screen.getByRole("button", { name: /User login/ })).toBeVisible();
    expect(screen.getByRole("button", { name: "Sign in as admin" })).toBeVisible();
    expect(screen.getByLabelText("Username")).toBeVisible();
    expect(screen.getByLabelText("Username")).toHaveValue("");
    expect(screen.queryByRole("banner")).not.toBeInTheDocument();
  });

  it("opens User login from an invitation link", async () => {
    const previous = `${window.location.pathname}${window.location.search}`;
    window.history.replaceState({}, "", "/login?invite=token-1");
    const client = {
      get: vi.fn((path: string) => {
        if (path.startsWith("/v1/auth/invite")) {
          return Promise.resolve(
            Response.json({
              username: "gelinas.cw@gmail.com",
              email: "gelinas.cw@gmail.com",
              display_name: "Chad",
              purpose: "invite",
            }),
          );
        }
        return Promise.resolve(new Response(null, { status: 401 }));
      }),
      post: vi.fn(),
      delete: vi.fn(),
      put: vi.fn(),
    } satisfies ApiClient;
    render(<App client={client} />);
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeVisible();
    expect(screen.getByRole("button", { name: /User login/ })).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByLabelText("New password")).toBeVisible();
    expect(screen.getByLabelText("Username")).toHaveValue("gelinas.cw@gmail.com");
    expect(screen.getByRole("button", { name: "Save password" })).toBeVisible();
    window.history.replaceState({}, "", previous);
  });

  it("sends a forgot-password login link from User login", async () => {
    const client = {
      get: vi.fn().mockResolvedValue(new Response(null, { status: 401 })),
      post: vi.fn((path: string) => {
        if (path === "/v1/auth/forgot-password") {
          return Promise.resolve(
            Response.json({
              mail_status: "If that email is authorized, we sent a login link. Check your email.",
            }),
          );
        }
        return Promise.resolve(new Response(null, { status: 400 }));
      }),
      delete: vi.fn(),
      put: vi.fn(),
    } satisfies ApiClient;
    render(<App client={client} />);
    fireEvent.click(await screen.findByRole("button", { name: /User login/ }));
    fireEvent.click(screen.getByRole("button", { name: "Forgot password?" }));
    expect(await screen.findByRole("heading", { name: "Forgot password" })).toBeVisible();
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "gelinas.cw@gmail.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Send login link" }));
    expect(await screen.findByText(/we sent a login link/i)).toBeVisible();
    expect(client.post).toHaveBeenCalledWith(
      "/v1/auth/forgot-password",
      expect.objectContaining({
        body: JSON.stringify({ email: "gelinas.cw@gmail.com" }),
      }),
    );
  });

  it("opens the admin workspace when an administrator uses User login", async () => {
    const client = {
      get: vi.fn((path: string) => {
        if (path === "/v1/auth/from-lab") {
          return Promise.resolve(
            Response.json({ token: "ems", username: "chad.gelinas@carbondc.io", role: "admin" }),
          );
        }
        return Promise.resolve(new Response(null, { status: 401 }));
      }),
      post: vi.fn((path: string) => {
        if (path === "/v1/auth/login") {
          return Promise.resolve(
            Response.json({ token: "ems", username: "chad.gelinas@carbondc.io", role: "admin" }),
          );
        }
        return Promise.resolve(
          Response.json({
            username: "chad.gelinas@carbondc.io",
            role: "admin",
            csrf_token: "csrf",
          }),
        );
      }),
      delete: vi.fn(),
      put: vi.fn(),
    } satisfies ApiClient;
    render(<App client={client} config={{ apiBaseUrl: "/api", simulationEnabled: true }} />);
    fireEvent.click(await screen.findByRole("button", { name: /User login/ }));
    fireEvent.change(screen.getByLabelText("Username"), {
      target: { value: "chad.gelinas@carbondc.io" },
    });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "carbon" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in as user" }));
    expect(await screen.findByRole("heading", { name: "Simulator Lab" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Users" })).toBeVisible();
  });

  it("remembers the theme after remounting, including on the sign-in screen", async () => {
    const client = {
      get: vi.fn().mockResolvedValue(new Response(null, { status: 401 })),
      post: vi.fn(),
      delete: vi.fn(), put: vi.fn(),
    } satisfies ApiClient;
    const first = render(<App client={client} />);
    await screen.findByRole("heading", { name: "Sign in" });
    fireEvent.click(screen.getByRole("switch", { name: "Dark theme" }));
    expect(localStorage.getItem("skid2.theme")).toBe("g100");
    first.unmount();
    render(<App client={client} />);
    expect(screen.getByRole("switch", { name: "Dark theme" })).toBeChecked();
    expect(document.documentElement.style.colorScheme).toBe("dark");
    await screen.findByRole("heading", { name: "Sign in" });
  });

  it("uses the system theme until an explicit preference is saved", () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({ matches: true })),
    );
    const client = {
      get: vi.fn(() => new Promise<Response>(() => undefined)),
      post: vi.fn(),
      delete: vi.fn(), put: vi.fn(),
    } satisfies ApiClient;
    const first = render(<App client={client} />);
    expect(screen.getByRole("switch", { name: "Dark theme" })).toBeChecked();
    fireEvent.click(screen.getByRole("switch", { name: "Dark theme" }));
    first.unmount();
    render(<App client={client} />);
    expect(screen.getByRole("switch", { name: "Dark theme" })).not.toBeChecked();
  });

  it("allows theme switching when browser storage is unavailable", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("Storage blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("Storage blocked");
    });
    const client = {
      get: vi.fn(() => new Promise<Response>(() => undefined)),
      post: vi.fn(),
      delete: vi.fn(), put: vi.fn(),
    } satisfies ApiClient;
    render(<App client={client} />);
    fireEvent.click(screen.getByRole("switch", { name: "Dark theme" }));
    expect(screen.getByRole("switch", { name: "Dark theme" })).toBeChecked();
  });

  it("keeps account actions in the header and preserves the session when sign-out fails", async () => {
    const client = {
      ...waitingClient(),
      post: vi
        .fn()
        .mockResolvedValueOnce(new Response(null, { status: 500 }))
        .mockResolvedValueOnce(new Response(null, { status: 204 })),
    } satisfies ApiClient;
    render(<App client={client} config={{ apiBaseUrl: "/api", simulationEnabled: true }} />);
    await screen.findByRole("heading", { name: "Simulator Lab" });
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Account" }));
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByText("Could not sign out. Please try again.")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Simulator Lab" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeVisible();
    expect(screen.queryByRole("banner")).not.toBeInTheDocument();
  });

  it("gives admin every workspace and a database user directory", async () => {
    const client = {
      get: vi.fn((path: string) => {
        if (path === "/v1/session") {
          return Promise.resolve(Response.json({ username: "admin", role: "admin" }));
        }
        if (path === "/v1/auth/from-lab") {
          return Promise.resolve(
            Response.json({ token: "ems-token", username: "admin", role: "admin" }),
          );
        }
        if (path === "/v1/auth/activity") {
          return Promise.resolve(Response.json([]));
        }
        if (path === "/v1/auth/users") {
          return Promise.resolve(
            Response.json([
              {
                username: "admin",
                role: "admin",
                email: "admin@carbondc.local",
                display_name: "Carbon DC Admin",
                company: "Carbon DC",
                system_name: "Skid 2",
                active: true,
              },
            ]),
          );
        }
        return new Promise<Response>(() => undefined);
      }),
      post: vi.fn(),
      delete: vi.fn(),
      put: vi.fn(),
    } satisfies ApiClient;
    render(<App client={client} config={{ apiBaseUrl: "/api", simulationEnabled: true }} />);
    expect(await screen.findByRole("heading", { name: "Simulator Lab" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Battery monitoring" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Operator EMS" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Users" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Users" }));
    expect(await screen.findByRole("heading", { name: "Authorized users" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Authorize and send email" })).toBeVisible();
    expect(await screen.findByText("Account activity")).toBeVisible();
    expect(await screen.findByText("admin@carbondc.local")).toBeVisible();
  });

  it("does not open the lab workspace for a leftover operator cookie", async () => {
    render(
      <App
        client={waitingClient("operator")}
        config={{ apiBaseUrl: "/api", simulationEnabled: true }}
      />,
    );
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Simulator Lab" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Users" })).not.toBeInTheDocument();
  });

  it("opens User login into the EMS UI with writable setpoints", async () => {
    class FakeSocket {
      addEventListener(): void {}
      close(): void {}
    }
    vi.stubGlobal("WebSocket", FakeSocket);
    const client = {
      get: vi.fn((path: string) => {
        if (path === "/v1/session") {
          return Promise.resolve(new Response(null, { status: 401 }));
        }
        if (path === "/v1/ems/mode") {
          return Promise.resolve(Response.json({ data_source: "sim" }));
        }
        if (path.startsWith("/v1/ems/telemetry/")) {
          return Promise.resolve(Response.json([]));
        }
        if (path === "/v1/ems/supervisor") {
          return Promise.resolve(
            Response.json({
              bus_voltage: 750,
              system_status: 0,
              available_power_kw: 100,
              heartbeats_ok: 6,
              heartbeats_total: 6,
              status_label: "ok",
              heartbeats: {},
            }),
          );
        }
        if (path === "/v1/ems/charger/status") {
          return Promise.resolve(
            Response.json({
              mode: "idle",
              allow_new_sessions: true,
              energy_cap_kwh: null,
              terminate_active: false,
              driver_message: "Welcome",
              soc: 0.5,
            }),
          );
        }
        if (/\/v1\/ems\/config\/\w+$/.test(path)) {
          const groupId = path.split("/").pop();
          return Promise.resolve(
            Response.json([
              {
                group_id: groupId,
                key: groupId === "charger" ? "station_message" : "pack_max_voltage",
                value: groupId === "charger" ? null : 730,
                value_text: groupId === "charger" ? "Welcome" : null,
                unit: groupId === "charger" ? "" : "V",
                updated_at: "2026-01-01T00:00:00Z",
                updated_by: "system",
              },
            ]),
          );
        }
        if (path.includes("/audit")) {
          return Promise.resolve(Response.json([]));
        }
        return Promise.resolve(Response.json({}));
      }),
      post: vi.fn((path: string) => {
        if (path === "/v1/auth/login") {
          return Promise.resolve(
            Response.json({ token: "user-token", username: "client", role: "operator" }),
          );
        }
        return Promise.resolve(Response.json({}));
      }),
      delete: vi.fn(),
      put: vi.fn(),
    } satisfies ApiClient;
    render(<App client={client} config={{ apiBaseUrl: "/api", simulationEnabled: true }} />);
    fireEvent.click(await screen.findByRole("button", { name: /User login/ }));
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "client" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in as user" }));
    expect(await screen.findByRole("heading", { name: "Overview" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Adjustments" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Simulator Lab" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Settings history" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Users" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Adjustments" }));
    expect(await screen.findByRole("heading", { name: "Adjustments" })).toBeVisible();
    expect(screen.getByText(/Every EMS read\/write setpoint/)).toBeVisible();
    expect(screen.getAllByRole("button", { name: "Apply" }).length).toBeGreaterThan(0);
  });

  it("keeps the Simulator Lab available after sign-in", async () => {
    render(
      <App client={waitingClient()} config={{ apiBaseUrl: "/api", simulationEnabled: true }} />,
    );

    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Simulator Lab" })).toBeVisible();
    expect(screen.queryByText("Current state")).not.toBeInTheDocument();
    expect(screen.queryByText(/Phase 2/i)).not.toBeInTheDocument();
  });

  it("shows monitoring when simulation is disabled", async () => {
    render(
      <App client={waitingClient()} config={{ apiBaseUrl: "/api", simulationEnabled: false }} />,
    );

    expect(await screen.findByRole("heading", { name: "Battery monitoring" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Simulator Lab" })).not.toBeInTheDocument();
    expect(screen.queryByText("Current state")).not.toBeInTheDocument();
  });
});

describe("Manual plant settings", () => {
  it("sends custom settings through the app and keeps preset requests compatible", async () => {
    let current = structuredClone(battery);
    const post = vi.fn(() => {
      current = {
        ...current,
        plant: {
          ...current.plant,
          configuration: {
            ...current.plant.configuration,
            bus: {
              nominal_voltage_v: 800,
              max_voltage_v: 900,
              capacitance_f: 2,
              loss_ratio: 0.1,
              tolerance_kw: 0.00001,
            },
            battery: {
              ...current.plant.configuration.battery,
              profile_id: "custom",
              initial_energy_kwh: 2800,
              charge_efficiency: 0.905,
              discharge_efficiency: 0.925,
              charge_limit_kw: 200,
              discharge_limit_kw: 250,
            },
          },
        },
      };
      return Promise.resolve(Response.json(current));
    });
    const get = vi.fn<NonNullable<ApiClient["get"]>>(() => new Promise<Response>(() => undefined));
    get.mockResolvedValueOnce(Response.json(current));
    const client: ApiClient = {
      get: (path, init) =>
        path === "/v1/session"
          ? Promise.resolve(Response.json({ username: "admin", role: "admin" }))
          : get(path, init),
      post,
      delete: vi.fn(), put: vi.fn(),
    };
    render(<App client={client} config={{ apiBaseUrl: "/api", simulationEnabled: true }} />);
    fireEvent.click(await screen.findByRole("button", { name: "Plant Lab" }));
    const changes: [string, string][] = [
      ["Starting battery level (%)", "80"],
      ["Charge efficiency (%)", "90.5"],
      ["Discharge efficiency (%)", "92.5"],
      ["Maximum charge power (kW)", "200"],
      ["Maximum discharge power (kW)", "250"],
      ["Starting voltage (VDC)", "800"],
      ["Maximum simulated voltage (VDC)", "900"],
      ["Bus capacitance (F)", "2"],
      ["Bus loss (%)", "10"],
      ["Calculation tolerance (kW)", "0.00001"],
    ];
    for (const [label, value] of changes) {
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    }
    expect(screen.getByLabelText("Battery setup")).toHaveValue("custom");
    expect(post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Apply and reset" }));
    await screen.findByText("Profile applied. The simulation has been reset.");
    expect(post).toHaveBeenCalledWith(
      "/v1/simulation/profile",
      expect.objectContaining({
        body: JSON.stringify({
          profile_id: "solar_cloud",
          seed: 0,
          battery_profile: "custom",
          battery_settings: {
            capacity_kwh: 3500,
            initial_level_percent: 80,
            charge_efficiency_percent: 90.5,
            discharge_efficiency_percent: 92.5,
            charge_limit_kw: 200,
            discharge_limit_kw: 250,
          },
          bus_settings: customBus,
        }),
      }),
    );
    expect(screen.getByLabelText("Starting battery level (%)")).toHaveValue(80);
    expect(screen.getByLabelText("Starting voltage (VDC)")).toHaveValue(800);
    expect(screen.getByLabelText("Bus loss (%)")).toHaveValue(10);
    fireEvent.change(screen.getByLabelText("Battery setup"), { target: { value: "battery_2700" } });
    expect(screen.getByLabelText("Usable capacity (kWh)")).toHaveValue(2700);
    expect(screen.getByLabelText("Starting battery level (%)")).toHaveValue(50);
    expect(screen.getByLabelText("Maximum discharge power (kW)")).toHaveValue(100);
    fireEvent.click(screen.getByRole("button", { name: "Apply and reset" }));
    await waitFor(() => {
      expect(post).toHaveBeenCalledTimes(2);
    });
    expect(post).toHaveBeenLastCalledWith(
      "/v1/simulation/profile",
      expect.objectContaining({
        body: JSON.stringify({
          profile_id: "solar_cloud",
          seed: 0,
          battery_profile: "battery_2700",
          battery_settings: null,
          bus_settings: customBus,
        }),
      }),
    );
  }, 30000);

  it("blocks empty and out-of-range entries while allowing decimal settings", () => {
    const apply = vi.fn(() => Promise.resolve());
    render(
      <PlantLab
        state={parseSimulationState(battery)}
        busy={false}
        run={() => Promise.resolve()}
        applyProfile={apply}
        download={() => Promise.resolve()}
      />,
    );
    const button = screen.getByRole("button", { name: "Apply and reset" });
    const checks: [string, string, string][] = [
      ["Usable capacity (kWh)", "", "3500.5"],
      ["Usable capacity (kWh)", "0", "3500.5"],
      ["Starting battery level (%)", "101", "80.5"],
      ["Charge efficiency (%)", "0", "90.5"],
      ["Discharge efficiency (%)", "101", "92.5"],
      ["Maximum charge power (kW)", "-1", "0"],
      ["Maximum discharge power (kW)", "-1", "200.5"],
    ];
    for (const [label, invalid, valid] of checks) {
      fireEvent.change(screen.getByLabelText(label), { target: { value: invalid } });
      expect(button).toBeDisabled();
      fireEvent.click(button);
      expect(apply).not.toHaveBeenCalled();
      fireEvent.change(screen.getByLabelText(label), { target: { value: valid } });
      expect(button).toBeEnabled();
    }
    fireEvent.click(button);
    expect(apply).toHaveBeenCalledWith(
      "solar_cloud",
      0,
      "custom",
      {
        capacity_kwh: 3500.5,
        initial_level_percent: 80.5,
        charge_efficiency_percent: 90.5,
        discharge_efficiency_percent: 92.5,
        charge_limit_kw: 0,
        discharge_limit_kw: 200.5,
      },
      defaultBus,
    );
  }, 30000);

  it("preserves drafts on refresh, reloads changed settings and locks editing while running", () => {
    const state = parseSimulationState(battery);
    const props = {
      state,
      busy: false,
      run: () => Promise.resolve(),
      applyProfile: vi.fn(() => Promise.resolve()),
      download: () => Promise.resolve(),
    };
    const { rerender } = render(<PlantLab {...props} />);
    fireEvent.change(screen.getByLabelText("Usable capacity (kWh)"), { target: { value: "4200" } });
    fireEvent.change(screen.getByLabelText("Starting voltage (VDC)"), { target: { value: "850" } });
    rerender(<PlantLab {...props} state={structuredClone(state)} />);
    expect(screen.getByLabelText("Usable capacity (kWh)")).toHaveValue(4200);
    expect(screen.getByLabelText("Starting voltage (VDC)")).toHaveValue(850);
    const custom = structuredClone(state);
    const settings = custom.plant.configuration.battery;
    if (settings === null) throw new Error("test battery settings are missing");
    settings.profile_id = "custom";
    settings.initial_energy_kwh = 2800;
    rerender(<PlantLab {...props} state={custom} />);
    expect(screen.getByLabelText("Starting battery level (%)")).toHaveValue(80);
    const restored = structuredClone(custom);
    if (restored.plant.configuration.battery === null)
      throw new Error("test battery settings are missing");
    restored.plant.configuration.battery.initial_energy_kwh = 2450;
    rerender(<PlantLab {...props} state={restored} />);
    expect(screen.getByLabelText("Starting battery level (%)")).toHaveValue(70);
    const restoredBus = structuredClone(restored);
    restoredBus.plant.configuration.bus.nominal_voltage_v = 700;
    restoredBus.plant.configuration.bus.loss_ratio = 0.025;
    rerender(<PlantLab {...props} state={restoredBus} />);
    expect(screen.getByLabelText("Starting voltage (VDC)")).toHaveValue(700);
    expect(screen.getByLabelText("Bus loss (%)")).toHaveValue(2.5);
    rerender(<PlantLab {...props} state={{ ...restored, lifecycle: "running" }} />);
    expect(screen.getByLabelText("Usable capacity (kWh)")).toBeDisabled();
    expect(screen.getByLabelText("Battery setup")).toBeDisabled();
    expect(screen.getByLabelText("Starting voltage (VDC)")).toBeDisabled();
    expect(screen.getByLabelText("Bus loss (%)")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Apply and reset" })).toBeDisabled();
  }, 30000);

  it("validates bus settings and applies them without requiring a battery", () => {
    const state = parseSimulationState(battery);
    state.plant.configuration.battery = null;
    const apply = vi.fn(() => Promise.resolve());
    render(
      <PlantLab
        state={state}
        busy={false}
        run={() => Promise.resolve()}
        applyProfile={apply}
        download={() => Promise.resolve()}
      />,
    );
    const button = screen.getByRole("button", { name: "Apply and reset" });
    const checks: [string, string, string][] = [
      ["Starting voltage (VDC)", "", "800.5"],
      ["Starting voltage (VDC)", "0", "800.5"],
      ["Maximum simulated voltage (VDC)", "800", "900.5"],
      ["Bus capacitance (F)", "0", "2.5"],
      ["Bus loss (%)", "100", "2.5"],
      ["Bus loss (%)", "-1", "0"],
      ["Calculation tolerance (kW)", "0", "0.00001"],
    ];
    for (const [label, invalid, valid] of checks) {
      fireEvent.change(screen.getByLabelText(label), { target: { value: invalid } });
      expect(button).toBeDisabled();
      fireEvent.click(button);
      expect(apply).not.toHaveBeenCalled();
      fireEvent.change(screen.getByLabelText(label), { target: { value: valid } });
      expect(button).toBeEnabled();
    }
    fireEvent.click(button);
    expect(apply).toHaveBeenCalledWith("solar_cloud", 0, null, null, {
      nominal_voltage_v: 800.5,
      max_voltage_v: 900.5,
      capacitance_f: 2.5,
      loss_percent: 0,
      tolerance_kw: 0.00001,
    });
  }, 30000);
});
