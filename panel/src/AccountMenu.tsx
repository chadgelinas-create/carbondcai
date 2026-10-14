import { useEffect, useRef, useState } from "react";
import { Button } from "@carbon/react";
import { Logout, UserAvatar } from "@carbon/icons-react";
import type { User } from "./Login";

const roles = { admin: "Administrator", operator: "Operator", viewer: "Viewer" };

export function AccountMenu({
  user,
  busy,
  signOut,
}: {
  user: User;
  busy: boolean;
  signOut: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      if (event.target instanceof Node && !container.current?.contains(event.target))
        setOpen(false);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
    };
  }, [open]);

  return (
    <div
      className="account-menu"
      ref={container}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          setOpen(false);
          trigger.current?.focus();
        }
      }}
    >
      <button
        className="account-trigger"
        ref={trigger}
        type="button"
        aria-label="Account"
        aria-expanded={open}
        aria-controls="account-panel"
        onClick={() => {
          setOpen(!open);
        }}
      >
        <UserAvatar size={20} aria-hidden="true" />
      </button>
      {open && (
        <section className="account-panel" id="account-panel" aria-label="Account details">
          <div className="account-identity">
            <strong>{user.username}</strong>
            <span>{roles[user.role]}</span>
          </div>
          <Button
            kind="ghost"
            size="sm"
            renderIcon={Logout}
            disabled={busy}
            onClick={() => void signOut()}
          >
            {busy ? "Signing out…" : "Sign out"}
          </Button>
        </section>
      )}
    </div>
  );
}
