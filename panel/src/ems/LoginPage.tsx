import { Button, Form, InlineNotification, PasswordInput, TextInput } from "@carbon/react";
import { ArrowLeft, ArrowRight } from "@carbon/icons-react";
import { useState } from "react";

import type { ApiClient } from "../api/client";
import { requestPasswordReset } from "./api";

interface LoginPageProps {
  client?: ApiClient;
  onSubmit: (username: string, password: string) => Promise<void>;
  error: string | null;
  onBack?: () => void;
}

export function LoginPage({ client, onSubmit, error, onBack }: LoginPageProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [forgot, setForgot] = useState(false);
  const [notice, setNotice] = useState("");
  const [localError, setLocalError] = useState("");

  async function sendResetLink() {
    if (!client) {
      return;
    }
    setBusy(true);
    setLocalError("");
    setNotice("");
    try {
      setNotice(await requestPasswordReset(client, username));
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : "Could not send the login link.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="sign-in auth-wireframe" aria-labelledby="ems-login-title">
      <div className="sign-in-heading">
        <h1 id="ems-login-title">{forgot ? "Forgot password" : "User login"}</h1>
        <p>
          {forgot
            ? "Enter your email. We will send a login link so you can set a new password and sign in."
            : "Operator EMS — plant UI and system setpoints. Lab and user directory stay with Admin login."}
        </p>
      </div>
      {error && !forgot ? (
        <InlineNotification kind="error" title="Sign-in failed" subtitle={error} hideCloseButton />
      ) : null}
      {localError ? <InlineNotification kind="error" title={localError} hideCloseButton /> : null}
      {notice ? <InlineNotification kind="success" title={notice} hideCloseButton /> : null}
      {forgot ? (
        <Form
          className="sign-in-form"
          onSubmit={(event) => {
            event.preventDefault();
            void sendResetLink();
          }}
        >
          <TextInput
            id="ems-forgot-email"
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
          <Button type="submit" disabled={busy || !client} renderIcon={ArrowRight}>
            {busy ? "Sending…" : "Send login link"}
          </Button>
          <Button
            kind="ghost"
            type="button"
            renderIcon={ArrowLeft}
            disabled={busy}
            onClick={() => {
              setForgot(false);
              setLocalError("");
              setNotice("");
            }}
          >
            Back to sign in
          </Button>
        </Form>
      ) : (
      <Form
        className="sign-in-form"
        onSubmit={(event) => {
          event.preventDefault();
          setBusy(true);
          void onSubmit(username, password).finally(() => {
            setBusy(false);
          });
        }}
      >
        <TextInput
          id="ems-username"
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
          id="ems-password"
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
          {busy ? "Signing in…" : "Sign in"}
        </Button>
        {client ? (
          <Button
            kind="ghost"
            type="button"
            disabled={busy}
            onClick={() => {
              setForgot(true);
              setLocalError("");
              setNotice("");
            }}
          >
            Forgot password?
          </Button>
        ) : null}
        {onBack ? (
          <Button kind="ghost" type="button" renderIcon={ArrowLeft} disabled={busy} onClick={onBack}>
            Back
          </Button>
        ) : null}
      </Form>
      )}
    </section>
  );
}
