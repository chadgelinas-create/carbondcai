import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  Button,
  Form,
  InlineLoading,
  InlineNotification,
  PasswordInput,
  TextInput,
} from "@carbon/react";
import { ArrowLeft, ArrowRight, EnergyRenewable } from "@carbon/icons-react";
import type { ApiClient } from "./api/client";
import { getRuntimeConfig } from "./config/runtime";
import {
  acceptInvite,
  exchangeLabSession,
  login as emsLogin,
  readInvite,
  requestPasswordReset,
  type AuthSession,
  type InvitePreview,
} from "./ems/api";
import { EmsApp } from "./ems/EmsApp";

export interface User {
  username: string;
  role: "viewer" | "operator" | "admin";
}

type Gate = "choose" | "user" | "admin" | "forgot";

const EMS_SESSION_KEY = "skid2.ems.session";

function inviteTokenFromUrl(): string {
  try {
    return new URLSearchParams(window.location.search).get("invite") ?? "";
  } catch {
    return "";
  }
}

export function Login({
  client,
  children,
  themeControl,
}: {
  client: ApiClient;
  themeControl: ReactNode;
  children: (
    user: User,
    session: {
      signOut: () => Promise<void>;
      busy: boolean;
      error: ReactNode;
      emsSession: AuthSession | null;
    },
  ) => ReactNode;
}) {
  const [user, setUser] = useState<User | null>(null);
  const [emsUser, setEmsUser] = useState(false);
  const [emsSession, setEmsSession] = useState<AuthSession | null>(null);
  const [gate, setGate] = useState<Gate>("choose");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [inviteToken, setInviteToken] = useState(inviteTokenFromUrl);
  const [invite, setInvite] = useState<InvitePreview | null>(null);
  const [invitePassword, setInvitePassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  const openAdminWorkspace = useCallback(
    async (next: User) => {
      setUser(next);
      try {
        const session = await exchangeLabSession(client);
        sessionStorage.setItem(EMS_SESSION_KEY, JSON.stringify(session));
        setEmsSession(session);
      } catch {
        setEmsSession(null);
      }
    },
    [client],
  );

  useEffect(() => {
    const ended = () => {
      setUser(null);
      setEmsUser(false);
      setEmsSession(null);
      sessionStorage.removeItem(EMS_SESSION_KEY);
      setGate("choose");
    };
    window.addEventListener("session-ended", ended);
    const controller = new AbortController();
    void client
      .get("/v1/session", { signal: controller.signal })
      .then(async (response) => {
        if (response.ok) {
          const next = (await response.json()) as User;
          if (next.role === "admin") {
            await openAdminWorkspace(next);
            return;
          }
        } else if (response.status !== 401) setError("Sign-in is unavailable. Please try again.");
        try {
          const raw = sessionStorage.getItem(EMS_SESSION_KEY);
          if (!raw) {
            return;
          }
          const stored = JSON.parse(raw) as AuthSession;
          if (stored.token && stored.role !== "admin") {
            setEmsUser(true);
          }
        } catch {
          sessionStorage.removeItem(EMS_SESSION_KEY);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setError("Cannot reach the server. Please try again.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
      window.removeEventListener("session-ended", ended);
    };
  }, [client, openAdminWorkspace]);

  useEffect(() => {
    if (!inviteToken) {
      return;
    }
    setGate("user");
    void readInvite(client, inviteToken)
      .then((preview) => {
        setInvite(preview);
        setUsername(preview.email || preview.username);
      })
      .catch((cause: unknown) => {
        setError(cause instanceof Error ? cause.message : "This login link is invalid or has expired");
      });
  }, [client, inviteToken]);

  function selectGate(next: Gate) {
    setGate(next);
    setError("");
    setNotice("");
    setPassword("");
    if (!inviteToken && next !== "forgot" && gate !== "forgot") {
      setUsername("");
    }
  }

  async function completeAdminSignIn(name: string, secret: string) {
    const response = await client.post("/v1/login", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: name, password: secret }),
    });
    if (!response.ok) {
      setError(
        response.status === 429
          ? "Too many attempts. Please wait a minute."
          : response.status === 401
            ? "Check your username and password, or try again after five minutes."
            : "Sign-in is unavailable. Please try again.",
      );
      return;
    }
    const next = (await response.json()) as User;
    if (next.role !== "admin") {
      await client.post("/v1/logout");
      setError("Administrator access required. Use User login for the operator account.");
      return;
    }
    await openAdminWorkspace(next);
    setPassword("");
  }

  async function signIn() {
    if (busy || gate === "choose") return;
    setBusy(true);
    setError("");
    try {
      if (gate === "user") {
        const session: AuthSession = await emsLogin(client, username, password);
        if (session.role === "admin") {
          await completeAdminSignIn(username, password);
          return;
        }
        sessionStorage.setItem(EMS_SESSION_KEY, JSON.stringify(session));
        setPassword("");
        setEmsUser(true);
        return;
      }
      await completeAdminSignIn(username, password);
    } catch {
      setError(
        gate === "user"
          ? "User sign-in failed. Check the user account and try again."
          : "Cannot reach the server. Please try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function sendResetLink() {
    if (busy) {
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const status = await requestPasswordReset(client, username);
      setNotice(status);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not send the login link.");
    } finally {
      setBusy(false);
    }
  }

  async function acceptAuthorization() {
    if (busy || !inviteToken) {
      return;
    }
    if (invitePassword !== confirmPassword) {
      setError("The passwords do not match.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const session = await acceptInvite(client, inviteToken, invitePassword);
      window.history.replaceState({}, "", "/login");
      setInviteToken("");
      setInvite(null);
      setInvitePassword("");
      setConfirmPassword("");
      setUsername(session.username);
      setGate("user");
      setNotice("Password saved. Sign in with User login.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not set the password.");
    } finally {
      setBusy(false);
    }
  }

  async function signOut() {
    setBusy(true);
    setError("");
    try {
      const response = await client.post("/v1/logout");
      if (!response.ok && response.status !== 401) throw new Error("Sign-out failed");
      setUser(null);
      setEmsUser(false);
      setEmsSession(null);
      sessionStorage.removeItem(EMS_SESSION_KEY);
      setGate("choose");
    } catch {
      setError("Could not sign out. Please try again.");
    } finally {
      setBusy(false);
    }
  }
  if (user) {
    return children(user, {
      signOut,
      busy,
      error: error ? <InlineNotification kind="error" title={error} hideCloseButton /> : null,
      emsSession,
    });
  }
  if (emsUser) {
    return (
      <EmsApp
        client={client}
        config={getRuntimeConfig()}
        themeControl={themeControl}
        onExit={() => {
          sessionStorage.removeItem(EMS_SESSION_KEY);
          setEmsUser(false);
          setGate("choose");
          setError("");
        }}
      />
    );
  }
  return (
    <main className="auth-page" id="main-content">
      <div className="auth-preferences">{themeControl}</div>
      <div className="auth-content">
        <div className="auth-brand">
          <EnergyRenewable size={32} aria-hidden="true" />
          <div>
            <span>Carbon DC</span>
            <strong>Skid 2 PMS/EMS</strong>
          </div>
        </div>
        {loading ? (
          <InlineLoading description="Checking sign-in…" />
        ) : (
          <section className="sign-in auth-wireframe" aria-labelledby="sign-in-title">
            <div className="sign-in-heading">
              <h1 id="sign-in-title">{gate === "forgot" ? "Forgot password" : "Sign in"}</h1>
              <p>
                {gate === "forgot"
                  ? "Enter your email. We will send a login link so you can set a new password and sign in."
                  : invite?.purpose === "password_change"
                    ? `Set a new password for ${invite.email}, then sign in with User login.`
                    : invite
                      ? `${invite.display_name} (${invite.email}) is authorized. Set your password, then sign in with User login.`
                      : "User login opens the EMS UI only. Admin login opens every workspace, including authorized users."}
              </p>
            </div>
            {error && <InlineNotification kind="error" title={error} hideCloseButton />}
            {notice && <InlineNotification kind="success" title={notice} hideCloseButton />}
            {gate !== "forgot" ? (
            <div className="auth-choice" role="group" aria-label="Login type">
              <button
                type="button"
                className={gate === "user" ? "auth-choice-button is-selected" : "auth-choice-button"}
                aria-pressed={gate === "user"}
                onClick={() => {
                  selectGate("user");
                }}
              >
                <strong>User login</strong>
                <span>EMS UI and setpoints</span>
              </button>
              <button
                type="button"
                className={
                  gate === "admin" ? "auth-choice-button is-selected" : "auth-choice-button"
                }
                aria-pressed={gate === "admin"}
                onClick={() => {
                  selectGate("admin");
                }}
              >
                <strong>Admin login</strong>
                <span>Every workspace</span>
              </button>
            </div>
            ) : null}
            {gate === "forgot" ? (
              <Form
                className="sign-in-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void sendResetLink();
                }}
              >
                <TextInput
                  id="forgot-email"
                  labelText="Email"
                  autoComplete="email"
                  type="email"
                  required
                  disabled={busy}
                  value={username}
                  onChange={(event) => {
                    setUsername(event.target.value);
                  }}
                />
                <Button type="submit" disabled={busy} renderIcon={ArrowRight}>
                  {busy ? "Sending…" : "Send login link"}
                </Button>
                <Button
                  kind="ghost"
                  type="button"
                  renderIcon={ArrowLeft}
                  disabled={busy}
                  onClick={() => {
                    selectGate("user");
                  }}
                >
                  Back to sign in
                </Button>
              </Form>
            ) : invite && gate === "user" ? (
              <Form
                className="sign-in-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void acceptAuthorization();
                }}
              >
                <TextInput
                  id="invite-username"
                  labelText="Username"
                  autoComplete="username"
                  readOnly
                  value={username}
                />
                <PasswordInput
                  id="invite-password"
                  labelText="New password"
                  autoComplete="new-password"
                  required
                  disabled={busy}
                  value={invitePassword}
                  onChange={(event) => {
                    setInvitePassword(event.target.value);
                  }}
                />
                <PasswordInput
                  id="invite-password-confirm"
                  labelText="Confirm password"
                  autoComplete="new-password"
                  required
                  disabled={busy}
                  value={confirmPassword}
                  onChange={(event) => {
                    setConfirmPassword(event.target.value);
                  }}
                />
                <Button type="submit" disabled={busy} renderIcon={ArrowRight}>
                  {busy ? "Saving…" : "Save password"}
                </Button>
              </Form>
            ) : gate !== "choose" ? (
              <Form
                className="sign-in-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void signIn();
                }}
              >
                <TextInput
                  id="username"
                  labelText="Username"
                  autoComplete="username"
                  required
                  disabled={busy}
                  value={username}
                  onChange={(event) => {
                    setUsername(event.target.value);
                  }}
                />
                <PasswordInput
                  id="password"
                  labelText="Password"
                  autoComplete="current-password"
                  required
                  disabled={busy}
                  value={password}
                  onChange={(event) => {
                    setPassword(event.target.value);
                  }}
                />
                <Button type="submit" disabled={busy} renderIcon={ArrowRight}>
                  {busy ? "Signing in…" : gate === "user" ? "Sign in as user" : "Sign in as admin"}
                </Button>
                <Button
                  kind="ghost"
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setError("");
                    setNotice("");
                    setPassword("");
                    setGate("forgot");
                  }}
                >
                  Forgot password?
                </Button>
              </Form>
            ) : null}
          </section>
        )}
        <p className="auth-footer">Energy monitoring &amp; simulation</p>
      </div>
    </main>
  );
}
