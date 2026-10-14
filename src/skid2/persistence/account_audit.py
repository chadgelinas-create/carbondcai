"""Append-only account activity. Rows are never updated or deleted."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import Connection, Engine, select
from sqlalchemy.exc import SQLAlchemyError

from skid2.persistence.metadata import account_audit

Action = Literal[
    "created",
    "disabled",
    "enabled",
    "updated",
    "password_reset",
    "signed_in",
    "sign_in_failed",
    "invited",
]
Gate = Literal["user", "admin", "directory"]

ACTIONS: frozenset[str] = frozenset(
    {
        "created",
        "disabled",
        "enabled",
        "updated",
        "password_reset",
        "signed_in",
        "sign_in_failed",
        "invited",
    }
)
GATES: frozenset[str] = frozenset({"user", "admin", "directory"})


@dataclass(frozen=True, slots=True)
class AccountAuditRow:
    occurred_at: datetime
    actor: str
    action: str
    subject: str
    gate: str
    detail: str


def write_account_audit(
    connection: Connection,
    *,
    actor: str,
    action: Action,
    subject: str,
    gate: Gate,
    detail: str = "",
) -> None:
    """Insert one audit row. Callers must not store passwords or hashes in detail."""

    if action not in ACTIONS or gate not in GATES:
        raise ValueError("Unknown account audit action or gate")
    connection.execute(
        account_audit.insert().values(
            occurred_at=datetime.now(UTC),
            actor=actor.strip()[:64] or "unknown",
            action=action,
            subject=subject.strip()[:64] or "unknown",
            gate=gate,
            detail=detail.strip()[:200],
        )
    )


def record_account_audit(engine: Engine, **values: str) -> None:
    """Best-effort write after sign-in so a missing table cannot block login."""

    try:
        with engine.begin() as connection:
            write_account_audit(
                connection,
                actor=values["actor"],
                action=values["action"],  # type: ignore[arg-type]
                subject=values["subject"],
                gate=values["gate"],  # type: ignore[arg-type]
                detail=values.get("detail", ""),
            )
    except (SQLAlchemyError, ValueError, KeyError):
        return


def list_account_audit(engine: Engine, limit: int = 100) -> list[AccountAuditRow]:
    capped = min(max(limit, 1), 500)
    with engine.connect() as connection:
        rows = (
            connection.execute(
                select(account_audit)
                .order_by(account_audit.c.occurred_at.desc(), account_audit.c.id.desc())
                .limit(capped)
            )
            .mappings()
            .all()
        )
    return [
        AccountAuditRow(
            occurred_at=row["occurred_at"],
            actor=str(row["actor"]),
            action=str(row["action"]),
            subject=str(row["subject"]),
            gate=str(row["gate"]),
            detail=str(row["detail"] or ""),
        )
        for row in rows
    ]
