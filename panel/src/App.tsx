import { useMemo, useState } from "react";
import { Content, Header, HeaderName, Button, SkipToContent, Theme, Toggle } from "@carbon/react";

import { createApiClient, type ApiClient } from "./api/client";
import { getRuntimeConfig, type RuntimeConfig } from "./config/runtime";
import { EmsApp } from "./ems/EmsApp";
import { SimulatorLab } from "./SimulatorLab";
import { Login } from "./Login";
import { Configuration } from "./Configuration";
import { Monitor } from "./Monitor";
import { AccountMenu } from "./AccountMenu";
import { AuthorizedUsers } from "./AuthorizedUsers";
import { useTheme } from "./useTheme";
import "./styles.scss";

interface AppProps {
  client?: ApiClient;
  config?: RuntimeConfig;
}

export function App({ client, config = getRuntimeConfig() }: AppProps) {
  const { apiBaseUrl } = config;
  const apiClient = useMemo(() => client ?? createApiClient({ apiBaseUrl }), [client, apiBaseUrl]);
  const [page, setPage] = useState<"monitor" | "lab" | "settings" | "ems" | "users">(
    config.simulationEnabled ? "lab" : "monitor",
  );
  const { theme, toggleTheme } = useTheme();
  const themeControl = (
    <div className="theme-control">
      <Toggle
        id="theme"
        labelText="Dark theme"
        hideLabel
        size="sm"
        toggled={theme === "g100"}
        onToggle={toggleTheme}
      />
    </div>
  );

  return (
    <Theme theme={theme} className="app-shell">
      <Login client={apiClient} themeControl={themeControl}>
        {(user, { signOut, busy, error, emsSession }) => (
          <>
            <SkipToContent href="#main-content" />
            <Header aria-label="Skid 2 PMS EMS">
              <HeaderName href="/" prefix="Carbon DC">
                Skid 2 PMS/EMS
              </HeaderName>
              {themeControl}
              <AccountMenu user={user} busy={busy} signOut={signOut} />
            </Header>
            <Content id="main-content">
              {error}
              <nav className="lab-navigation" aria-label="Workspace">
                <Button
                  kind={page === "monitor" ? "primary" : "tertiary"}
                  onClick={() => {
                    setPage("monitor");
                  }}
                >
                  Battery monitoring
                </Button>
                {config.simulationEnabled && user.role !== "viewer" && (
                  <Button
                    kind={page === "lab" ? "primary" : "tertiary"}
                    onClick={() => {
                      setPage("lab");
                    }}
                  >
                    Simulator Lab
                  </Button>
                )}
                <Button
                  kind={page === "settings" ? "primary" : "tertiary"}
                  onClick={() => {
                    setPage("settings");
                  }}
                >
                  Settings history
                </Button>
                <Button
                  kind={page === "ems" ? "primary" : "tertiary"}
                  onClick={() => {
                    setPage("ems");
                  }}
                >
                  Operator EMS
                </Button>
                {user.role === "admin" ? (
                  <Button
                    kind={page === "users" ? "primary" : "tertiary"}
                    onClick={() => {
                      setPage("users");
                    }}
                  >
                    Users
                  </Button>
                ) : null}
              </nav>
              {page === "users" && user.role === "admin" ? (
                <AuthorizedUsers client={apiClient} token={emsSession?.token} />
              ) : page === "ems" ? (
                <EmsApp
                  client={apiClient}
                  config={config}
                  initialSession={emsSession}
                  embedded
                />
              ) : page === "settings" ? (
                <Configuration
                  client={apiClient}
                  admin={user.role === "admin"}
                  simulationEnabled={config.simulationEnabled && user.role !== "viewer"}
                />
              ) : page === "lab" && config.simulationEnabled && user.role !== "viewer" ? (
                <SimulatorLab client={apiClient} />
              ) : (
                <Monitor client={apiClient} apiBaseUrl={apiBaseUrl} admin={user.role === "admin"} />
              )}
            </Content>
          </>
        )}
      </Login>
    </Theme>
  );
}
