import {
  Button,
  Header,
  HeaderName,
  Tag,
  Theme,
} from "@carbon/react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AuthorizedUsers } from "../AuthorizedUsers";
import type { ApiClient } from "../api/client";
import {
  connectEmsSocket,
  fetchCharger,
  fetchConfig,
  fetchConfigAudit,
  fetchMode,
  fetchSupervisor,
  fetchTelemetry,
  injectCommsLoss,
  setChargerPort,
  setMode,
  writeConfig,
  type AuthSession,
  type ChargerStatus,
  type ConfigAuditRow,
  type ConfigItem,
  type SupervisorStatus,
  type TelemetryPoint,
} from "./api";
import { AdjustmentsPage } from "./pages/AdjustmentsPage";
import { BessPage } from "./pages/BessPage";
import { ChargerPage } from "./pages/ChargerPage";
import { ConvertersPage } from "./pages/ConvertersPage";
import { HistorianPage } from "./pages/HistorianPage";
import { OverviewPage } from "./pages/OverviewPage";
import { SolarPage } from "./pages/SolarPage";
import { StabilitiPage } from "./pages/StabilitiPage";

type PageId =
  | "overview"
  | "bess"
  | "converters"
  | "solar"
  | "charger"
  | "stabiliti"
  | "adjustments"
  | "historian"
  | "users";

interface EmsShellProps {
  client: ApiClient;
  session: AuthSession;
  apiBaseUrl: string;
  onLogout: () => void;
  embedded?: boolean;
}

export function EmsShell({
  client,
  session,
  apiBaseUrl,
  onLogout,
  embedded = false,
}: EmsShellProps) {
  const [page, setPage] = useState<PageId>("overview");
  const [points, setPoints] = useState<TelemetryPoint[]>([]);
  const [mode, setModeState] = useState<"sim" | "live">("sim");
  const [supervisor, setSupervisor] = useState<SupervisorStatus | null>(null);
  const [charger, setCharger] = useState<ChargerStatus | null>(null);
  const [configs, setConfigs] = useState<Record<string, ConfigItem[]>>({});
  const [audits, setAudits] = useState<Record<string, ConfigAuditRow[]>>({});
  const [lastCommand, setLastCommand] = useState<ConfigItem | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const byId = useMemo(() => {
    const map = new Map<string, TelemetryPoint>();
    for (const point of points) {
      map.set(point.point_id, point);
    }
    return map;
  }, [points]);

  const refresh = useCallback(async () => {
    const [all, nextMode, nextSupervisor, nextCharger] = await Promise.all([
      fetchTelemetry(client, session.token, "all"),
      fetchMode(client, session.token),
      fetchSupervisor(client, session.token),
      fetchCharger(client, session.token),
    ]);
    setPoints(all);
    setModeState(nextMode);
    setSupervisor(nextSupervisor);
    setCharger(nextCharger);
  }, [client, session.token]);

  const loadConfig = useCallback(
    async (groupId: string) => {
      const [items, audit] = await Promise.all([
        fetchConfig(client, session.token, groupId),
        fetchConfigAudit(client, session.token, groupId).catch(() => [] as ConfigAuditRow[]),
      ]);
      setConfigs((previous) => ({ ...previous, [groupId]: items }));
      setAudits((previous) => ({ ...previous, [groupId]: audit }));
    },
    [client, session.token],
  );

  useEffect(() => {
    void refresh().catch((error: unknown) => {
      setMessage(error instanceof Error ? error.message : "Refresh failed");
    });
    const timer = window.setInterval(() => {
      void refresh().catch(() => undefined);
    }, 2000);
    return () => {
      window.clearInterval(timer);
    };
  }, [refresh]);

  useEffect(() => {
    const socket = connectEmsSocket(apiBaseUrl, session.token, (payload) => {
      const body = payload as {
        type?: string;
        points?: TelemetryPoint[];
        data_source?: "sim" | "live";
      };
      if (body.data_source) {
        setModeState(body.data_source);
      }
      if (body.points) {
        setPoints((previous) => {
          const map = new Map(previous.map((point) => [point.point_id, point]));
          for (const point of body.points ?? []) {
            map.set(point.point_id, point);
          }
          return [...map.values()];
        });
      }
    });
    return () => {
      socket.close();
    };
  }, [apiBaseUrl, session.token]);

  useEffect(() => {
    void Promise.all([
      loadConfig("bms"),
      loadConfig("solar"),
      loadConfig("cnvtr"),
      loadConfig("charger"),
      loadConfig("stabiliti"),
      loadConfig("supervisor"),
    ]);
  }, [loadConfig]);

  const applyConfig = async (groupId: string, key: string, value: number | string) => {
    setMessage(null);
    try {
      const result = await writeConfig(client, session.token, groupId, key, value);
      setLastCommand(result);
      await loadConfig(groupId);
      setMessage(result.message || `${groupId}.${key} ${result.outcome ?? "applied"}`);
    } catch (error) {
      setLastCommand({
        group_id: groupId,
        key,
        value: typeof value === "number" ? value : null,
        value_text: typeof value === "string" ? value : null,
        unit: "",
        updated_at: new Date().toISOString(),
        updated_by: session.username,
        outcome: "rejected",
        requested: String(value),
        effective: null,
        message: error instanceof Error ? error.message : "Config write failed",
      });
      setMessage(error instanceof Error ? error.message : "Config write failed");
    }
  };

  const canWrite = session.role !== "viewer";
  const isAdmin = session.role === "admin";
  const pages: [PageId, string][] = [
    ["overview", "Overview"],
    ["bess", "BESS"],
    ["converters", "Converters"],
    ["solar", "Solar"],
    ["charger", "EV Charge"],
    ["stabiliti", "Stabiliti"],
    ["adjustments", "Adjustments"],
    ["historian", "Historian"],
  ];
  if (isAdmin) {
    pages.push(["users", "Users"]);
  }

  return (
    <Theme theme="white">
      <Header aria-label="Carbon DC EMS">
        <HeaderName href="/" prefix="Carbon DC">
          EMS
        </HeaderName>
        <nav className="ems-page-nav" aria-label="EMS pages">
          {pages.map(([id, label]) => (
            <button
              key={id}
              type="button"
              className={page === id ? "ems-view-btn active" : "ems-view-btn"}
              onClick={() => {
                setPage(id);
              }}
            >
              {label}
            </button>
          ))}
        </nav>
        <div className="ems-header-actions">
          <Tag type={mode === "sim" ? "blue" : "green"} size="sm">
            {mode === "sim" ? "Simulator data" : "Live data"}
          </Tag>
          <Tag type="gray" size="sm">
            {session.username}
          </Tag>
          {isAdmin ? (
            <div className="ems-mode-switch" role="group" aria-label="Backend data source">
              <Button
                kind={mode === "sim" ? "primary" : "ghost"}
                size="sm"
                onClick={() => {
                  if (mode === "sim") {
                    return;
                  }
                  void setMode(client, session.token, "sim")
                    .then(setModeState)
                    .catch((error: unknown) => {
                      setMessage(error instanceof Error ? error.message : "Mode change failed");
                    });
                }}
              >
                Simulator
              </Button>
              <Button
                kind={mode === "live" ? "primary" : "ghost"}
                size="sm"
                onClick={() => {
                  if (mode === "live") {
                    return;
                  }
                  void setMode(client, session.token, "live")
                    .then(setModeState)
                    .catch((error: unknown) => {
                      setMessage(error instanceof Error ? error.message : "Mode change failed");
                    });
                }}
              >
                Live
              </Button>
            </div>
          ) : null}
          {embedded ? null : (
            <Button kind="ghost" size="sm" onClick={onLogout}>
              Sign out
            </Button>
          )}
        </div>
      </Header>
      <main className="ems-main" id="main-content">
        {message ? <p className="screen-message">{message}</p> : null}
        {page === "overview" ? (
          <OverviewPage
            byId={byId}
            supervisor={supervisor}
            charger={charger}
            onFault={
              isAdmin
                ? async (deviceId, clear) => {
                    await injectCommsLoss(client, session.token, deviceId, clear);
                    await refresh();
                  }
                : undefined
            }
          />
        ) : null}
        {page === "bess" ? (
          <BessPage
            byId={byId}
            config={configs.bms ?? []}
            onApply={applyConfig}
            canWrite={canWrite}
          />
        ) : null}
        {page === "converters" ? (
          <ConvertersPage
            byId={byId}
            config={configs.cnvtr ?? []}
            audit={audits.cnvtr ?? []}
            lastCommand={lastCommand}
            onApply={applyConfig}
            canWrite={canWrite}
          />
        ) : null}
        {page === "solar" ? (
          <SolarPage
            byId={byId}
            config={configs.solar ?? []}
            audit={audits.solar ?? []}
            lastCommand={lastCommand}
            onApply={applyConfig}
            canWrite={canWrite}
          />
        ) : null}
        {page === "charger" ? (
          <ChargerPage
            byId={byId}
            charger={charger}
            config={configs.charger ?? []}
            onApply={applyConfig}
            canWrite={canWrite}
            onPort={async (portId, occupied, requestedKw) => {
              const next = await setChargerPort(client, session.token, portId, occupied, requestedKw);
              setCharger(next);
            }}
          />
        ) : null}
        {page === "stabiliti" ? (
          <StabilitiPage
            byId={byId}
            config={configs.stabiliti ?? []}
            audit={audits.stabiliti ?? []}
            lastCommand={lastCommand}
            onApply={applyConfig}
            canWrite={canWrite}
            onFault={
              isAdmin
                ? async (deviceId, clear) => {
                    await injectCommsLoss(client, session.token, deviceId, clear);
                    await refresh();
                  }
                : undefined
            }
          />
        ) : null}
        {page === "adjustments" ? (
          <AdjustmentsPage configs={configs} onApply={applyConfig} canWrite={canWrite} />
        ) : null}
        {page === "historian" ? <HistorianPage byId={byId} client={client} token={session.token} /> : null}
        {page === "users" && isAdmin ? (
          <AuthorizedUsers client={client} token={session.token} />
        ) : null}
      </main>
    </Theme>
  );
}
