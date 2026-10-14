import { useCallback, useEffect, useState } from "react";
import {
  Button,
  Form,
  InlineNotification,
  Select,
  SelectItem,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableHeader,
  TableRow,
  TextInput,
} from "@carbon/react";

import type { ApiClient } from "./api/client";
import {
  createUser,
  exchangeLabSession,
  listActivity,
  listUsers,
  sendUserInvite,
  setUserActive,
  type AccountActivity,
  type StoredAccount,
} from "./ems/api";

const activityLabels: Record<string, string> = {
  created: "Created",
  disabled: "Disabled",
  enabled: "Enabled",
  updated: "Updated",
  password_reset: "Password reset",
  signed_in: "Signed in",
  sign_in_failed: "Sign-in failed",
  invited: "Invitation sent",
};

const gateLabels: Record<string, string> = {
  user: "User login",
  admin: "Admin login",
  directory: "User directory",
};

export function AuthorizedUsers({
  client,
  token,
}: {
  client: ApiClient;
  token?: string;
}) {
  const [accounts, setAccounts] = useState<StoredAccount[]>([]);
  const [activity, setActivity] = useState<AccountActivity[]>([]);
  const [accessToken, setAccessToken] = useState(token ?? "");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [role, setRole] = useState("operator");
  const [inviteUrl, setInviteUrl] = useState("");

  const refresh = useCallback(
    async (nextToken: string) => {
      const [rows, events] = await Promise.all([
        listUsers(client, nextToken),
        listActivity(client, nextToken),
      ]);
      setAccounts(rows);
      setActivity(events);
    },
    [client],
  );

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const nextToken = token || (await exchangeLabSession(client)).token;
        if (cancelled) {
          return;
        }
        setAccessToken(nextToken);
        await refresh(nextToken);
      } catch (cause) {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : "Could not load authorized users.");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, refresh, token]);

  async function onCreate() {
    if (busy || !accessToken) {
      return;
    }
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const created = await createUser(client, accessToken, {
        username: username || email,
        email,
        display_name: displayName || username || email,
        company: "Carbon DC",
        system_name: "Skid 2",
        role,
      });
      setUsername("");
      setEmail("");
      setDisplayName("");
      setRole("operator");
      setInviteUrl(created.invite_url ?? "");
      await refresh(accessToken);
      setMessage(
        created.mail_status ||
          `Invitation sent to ${created.email}. They confirm access and set a password from that email.`,
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not store the user.");
    } finally {
      setBusy(false);
    }
  }

  async function onInvite(account: StoredAccount) {
    if (busy || !accessToken) {
      return;
    }
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const sent = await sendUserInvite(client, accessToken, account.username);
      setInviteUrl(sent.invite_url ?? "");
      await refresh(accessToken);
      setMessage(sent.mail_status || `Invitation sent to ${sent.email}.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not send the invitation.");
    } finally {
      setBusy(false);
    }
  }

  async function onToggle(account: StoredAccount) {
    if (busy || !accessToken) {
      return;
    }
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const updated = await setUserActive(client, accessToken, account.username, !account.active);
      await refresh(accessToken);
      setMessage(
        updated.active
          ? `${updated.username} can sign in again.`
          : `${updated.username} can no longer sign in.`,
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not update the user.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="lab authorized-users" aria-labelledby="users-title">
      <div className="page-title">
        <h1 id="users-title">Authorized users</h1>
        <p>
          Authorize a client with their email. They receive a login link to open this sign-in
          page, set or change their password, and use User login. Operator can change every
          system setpoint. Viewer is read-only.
        </p>
      </div>
      {error ? <InlineNotification kind="error" title={error} hideCloseButton /> : null}
      {message ? <InlineNotification kind="success" title={message} hideCloseButton /> : null}
      {inviteUrl ? (
        <p className="authorized-invite-link">
          Login link for testing:{" "}
          <a href={inviteUrl}>{inviteUrl}</a>
        </p>
      ) : null}
      <Form
        className="authorized-users-form"
        onSubmit={(event) => {
          event.preventDefault();
          void onCreate();
        }}
      >
        <h2>Authorize a user</h2>
        <TextInput
          id="new-username"
          labelText="Username (defaults to email)"
          disabled={busy}
          value={username}
          onChange={(event) => {
            setUsername(event.target.value);
          }}
        />
        <TextInput
          id="new-email"
          labelText="Email"
          type="email"
          required
          disabled={busy}
          value={email}
          onChange={(event) => {
            setEmail(event.target.value);
          }}
        />
        <TextInput
          id="new-display-name"
          labelText="Display name"
          disabled={busy}
          value={displayName}
          onChange={(event) => {
            setDisplayName(event.target.value);
          }}
        />
        <Select
          id="new-role"
          labelText="Role"
          disabled={busy}
          value={role}
          onChange={(event) => {
            setRole(event.target.value);
          }}
        >
          <SelectItem value="operator" text="Operator — EMS UI and setpoints" />
          <SelectItem value="viewer" text="Viewer — EMS UI read only" />
          <SelectItem value="manager" text="Manager — EMS UI and setpoints" />
          <SelectItem value="admin" text="Admin — full workspace" />
        </Select>
        <Button type="submit" disabled={busy}>
          {busy ? "Sending…" : "Authorize and send email"}
        </Button>
      </Form>
      <TableContainer title="Database accounts">
        <Table>
          <TableHead>
            <TableRow>
              <TableHeader>Username</TableHeader>
              <TableHeader>Email</TableHeader>
              <TableHeader>Role</TableHeader>
              <TableHeader>Access</TableHeader>
              <TableHeader>Action</TableHeader>
            </TableRow>
          </TableHead>
          <TableBody>
            {accounts.map((account) => (
              <TableRow key={account.username}>
                <TableCell>{account.username}</TableCell>
                <TableCell>{account.email || "—"}</TableCell>
                <TableCell>{account.role}</TableCell>
                <TableCell>{account.active ? "Authorized" : "Disabled"}</TableCell>
                <TableCell>
                  <Button
                    kind="ghost"
                    size="sm"
                    disabled={busy || !account.active || !account.email}
                    onClick={() => {
                      void onInvite(account);
                    }}
                  >
                    Send email
                  </Button>
                  <Button
                    kind="ghost"
                    size="sm"
                    disabled={busy}
                    onClick={() => {
                      void onToggle(account);
                    }}
                  >
                    {account.active ? "Disable" : "Enable"}
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
      <TableContainer title="Account activity">
        <Table>
          <TableHead>
            <TableRow>
              <TableHeader>When</TableHeader>
              <TableHeader>Who</TableHeader>
              <TableHeader>Action</TableHeader>
              <TableHeader>Account</TableHeader>
              <TableHeader>Where</TableHeader>
            </TableRow>
          </TableHead>
          <TableBody>
            {activity.map((event) => (
              <TableRow key={`${event.occurred_at}-${event.actor}-${event.action}-${event.subject}`}>
                <TableCell>
                  {Number.isNaN(Date.parse(event.occurred_at))
                    ? event.occurred_at
                    : new Date(event.occurred_at).toLocaleString()}
                </TableCell>
                <TableCell>{event.actor}</TableCell>
                <TableCell>{activityLabels[event.action] ?? event.action}</TableCell>
                <TableCell>{event.subject}</TableCell>
                <TableCell>{gateLabels[event.gate] ?? event.gate}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    </section>
  );
}
