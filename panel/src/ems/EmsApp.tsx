import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { EnergyRenewable } from "@carbon/icons-react";

import { createApiClient, type ApiClient } from "../api/client";
import { getRuntimeConfig, type RuntimeConfig } from "../config/runtime";
import { login, type AuthSession } from "./api";
import { EmsShell } from "./EmsShell";
import { LoginPage } from "./LoginPage";

const SESSION_KEY = "skid2.ems.session";

interface EmsAppProps {
  client?: ApiClient;
  config?: RuntimeConfig;
  themeControl?: ReactNode;
  onExit?: () => void;
  initialSession?: AuthSession | null;
  embedded?: boolean;
}

function loadSession(): AuthSession | null {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (!raw) {
      return null;
    }
    return JSON.parse(raw) as AuthSession;
  } catch {
    return null;
  }
}

export function EmsApp({
  client,
  config = getRuntimeConfig(),
  themeControl,
  onExit,
  initialSession,
  embedded = false,
}: EmsAppProps) {
  const apiClient = useMemo(
    () => client ?? createApiClient({ apiBaseUrl: config.apiBaseUrl }),
    [client, config.apiBaseUrl],
  );
  const [session, setSession] = useState<AuthSession | null>(
    () => initialSession ?? loadSession(),
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (initialSession) {
      setSession(initialSession);
    }
  }, [initialSession]);

  useEffect(() => {
    if (session) {
      sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
    } else if (!embedded) {
      sessionStorage.removeItem(SESSION_KEY);
    }
  }, [embedded, session]);

  const onLogin = useCallback(
    async (username: string, password: string) => {
      setError(null);
      try {
        const next = await login(apiClient, username, password);
        setSession(next);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Login failed");
      }
    },
    [apiClient],
  );

  if (!session) {
    if (embedded) {
      return (
        <p className="screen-message">
          Admin EMS access could not be opened. Sign out and sign in as admin again.
        </p>
      );
    }
    return (
      <main className="auth-page" id="main-content">
        {themeControl ? <div className="auth-preferences">{themeControl}</div> : null}
        <div className="auth-content">
          <div className="auth-brand">
            <EnergyRenewable size={32} aria-hidden="true" />
            <div>
              <span>Carbon DC</span>
              <strong>Skid 2 PMS/EMS</strong>
            </div>
          </div>
          <LoginPage client={apiClient} onSubmit={onLogin} error={error} onBack={onExit} />
          <p className="auth-footer">Energy monitoring &amp; simulation</p>
        </div>
      </main>
    );
  }

  return (
    <EmsShell
      client={apiClient}
      session={session}
      apiBaseUrl={config.apiBaseUrl}
      embedded={embedded}
      onLogout={() => {
        setSession(null);
        onExit?.();
      }}
    />
  );
}
