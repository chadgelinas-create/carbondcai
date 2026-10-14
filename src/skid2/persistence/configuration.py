"""Immutable revisions and serialized, durable configuration actions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from skid2.persistence.metadata import (
    app_config,
)
from skid2.persistence.metadata import (
    configuration_action as actions,
)
from skid2.persistence.metadata import (
    configuration_approval as approvals,
)
from skid2.persistence.metadata import (
    configuration_head as heads,
)
from skid2.persistence.metadata import (
    configuration_revision as revisions,
)


class ConfigurationConflictError(ValueError):
    """The requested change cannot safely replace current settings."""


class ConfigurationRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def head(self, scope: str) -> dict[str, Any]:
        with self.engine.connect() as connection:
            row = connection.execute(select(heads).where(heads.c.scope == scope)).mappings().first()
        return (
            dict(row)
            if row
            else {
                "scope": scope,
                "revision_id": None,
                "generation": 0,
                "pending_action": None,
            }
        )

    def revision(self, revision_id: int) -> dict[str, Any]:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(revisions, approvals.c.approved_by, approvals.c.approved_at)
                    .outerjoin(approvals)
                    .where(revisions.c.id == revision_id)
                )
                .mappings()
                .first()
            )
        if row is None:
            raise ConfigurationConflictError(
                "These saved settings no longer exist. Reload the page."
            )
        return dict(row)

    def history(self, scope: str, before: int | None = None) -> list[dict[str, Any]]:
        query = (
            select(revisions, approvals.c.approved_by, approvals.c.approved_at)
            .outerjoin(approvals)
            .where(revisions.c.scope == scope)
        )
        if before is not None:
            query = query.where(revisions.c.id < before)
        with self.engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    query.order_by(revisions.c.id.desc()).limit(50)
                ).mappings()
            ]

    def create(
        self, scope: str, name: str, reason: str, payload: dict[str, Any], actor: str, expected: int
    ) -> dict[str, Any]:
        error = None
        revision_id = None
        with self.engine.begin() as connection:
            connection.execute(pg_insert(heads).values(scope=scope).on_conflict_do_nothing())
            head = (
                connection.execute(select(heads).where(heads.c.scope == scope).with_for_update())
                .mappings()
                .one()
            )
            if head["generation"] != expected or head["pending_action"]:
                error = "Settings changed while you were editing. Reload and review your changes."
            if error is None:
                revision_id = connection.execute(
                    insert(revisions)
                    .values(
                        scope=scope,
                        name=name,
                        reason=reason,
                        payload=payload,
                        created_by=actor,
                    )
                    .returning(revisions.c.id)
                ).scalar_one()
            connection.execute(
                insert(actions).values(
                    id=str(uuid4()),
                    scope=scope,
                    actor=actor,
                    kind="save_draft",
                    outcome="rejected" if error else "accepted",
                    before=dict(head),
                    requested=payload,
                    effective={"revision_id": head["revision_id"]},
                    message=error or "Settings saved for review; running settings are unchanged.",
                    finished_at=datetime.now(UTC),
                )
            )
        if error:
            raise ConfigurationConflictError(error)
        assert revision_id is not None  # noqa: S101 - insert returns the generated identity
        return self.revision(revision_id)

    def approve(self, revision_id: int, actor: str) -> None:
        revision = self.revision(revision_id)
        with self.engine.begin() as connection:
            inserted = connection.execute(
                pg_insert(approvals)
                .values(
                    revision_id=revision_id,
                    approved_by=actor,
                )
                .on_conflict_do_nothing()
                .returning(approvals.c.revision_id)
            ).scalar_one_or_none()
            if inserted is not None:
                connection.execute(
                    insert(actions).values(
                        id=str(uuid4()),
                        scope=revision["scope"],
                        actor=actor,
                        kind="approve",
                        outcome="accepted",
                        requested={"revision_id": revision_id},
                        message="Settings approved. Apply them when ready.",
                        finished_at=datetime.now(UTC),
                    )
                )

    def begin(self, revision_id: int, expected: int, actor: str, kind: str) -> dict[str, Any]:
        revision = self.revision(revision_id)
        action_id = str(uuid4())
        error = None
        with self.engine.begin() as connection:
            head = (
                connection.execute(
                    select(heads).where(heads.c.scope == revision["scope"]).with_for_update()
                )
                .mappings()
                .one()
            )
            if head["generation"] != expected or head["pending_action"]:
                error = "Another change is in progress or settings have changed. Reload first."
            elif revision["approved_by"] is None:
                error = "An administrator must approve these settings before applying them."
            connection.execute(
                insert(actions).values(
                    id=action_id,
                    scope=revision["scope"],
                    actor=actor,
                    kind=kind,
                    outcome="rejected" if error else "pending",
                    before=dict(head),
                    requested={"revision_id": revision_id, "payload": revision["payload"]},
                    effective={"revision_id": head["revision_id"]},
                    message=error or "Installing approved settings in the application.",
                    finished_at=datetime.now(UTC) if error else None,
                )
            )
            if error is None:
                connection.execute(
                    update(heads)
                    .where(heads.c.scope == revision["scope"])
                    .values(pending_action=action_id)
                )
        if error:
            raise ConfigurationConflictError(error)
        return {"id": action_id, "generation": expected + 1, "revision": revision}

    def finish(self, action: dict[str, Any], error: str | None = None) -> None:
        revision = action["revision"]
        scope = revision["scope"]
        with self.engine.begin() as connection:
            head = (
                connection.execute(select(heads).where(heads.c.scope == scope).with_for_update())
                .mappings()
                .one()
            )
            if head["pending_action"] != action["id"]:
                raise ConfigurationConflictError(
                    "This change is no longer pending. Reload settings."
                )
            if error is None:
                key = "monitor" if scope == "monitor" else "simulation.settings"
                projection = pg_insert(app_config).values(
                    key=key,
                    value=revision["payload"],
                    revision=action["generation"],
                )
                connection.execute(
                    projection.on_conflict_do_update(
                        index_elements=[app_config.c.key],
                        set_={
                            "value": projection.excluded.value,
                            "revision": projection.excluded.revision,
                        },
                    )
                )
                connection.execute(
                    update(heads)
                    .where(heads.c.scope == scope)
                    .values(
                        revision_id=revision["id"],
                        generation=action["generation"],
                        pending_action=None,
                    )
                )
            else:
                connection.execute(
                    update(heads).where(heads.c.scope == scope).values(pending_action=None)
                )
            connection.execute(
                update(actions)
                .where(actions.c.id == action["id"])
                .values(
                    outcome="failed" if error else "applied",
                    effective={"revision_id": head["revision_id"] if error else revision["id"]},
                    message=error
                    or "Installed in the application. Device readings are reported separately.",
                    finished_at=datetime.now(UTC),
                )
            )

    def record(
        self,
        scope: str,
        actor: str,
        kind: str,
        outcome: str,
        message: str,
        requested: dict[str, Any] | None = None,
        before: dict[str, Any] | None = None,
        effective: dict[str, Any] | None = None,
    ) -> str:
        action_id = str(uuid4())
        with self.engine.begin() as connection:
            connection.execute(
                insert(actions).values(
                    id=action_id,
                    scope=scope,
                    actor=actor,
                    kind=kind,
                    outcome=outcome,
                    message=message[:1000],
                    requested=requested,
                    before=before,
                    effective=effective,
                    finished_at=None if outcome == "pending" else datetime.now(UTC),
                )
            )
        return action_id

    def finish_command(
        self, action_id: str, outcome: str, message: str, effective: dict[str, Any] | None
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                update(actions)
                .where(
                    actions.c.id == action_id,
                    actions.c.outcome == "pending",
                )
                .values(
                    outcome=outcome,
                    message=message[:1000],
                    effective=effective,
                    finished_at=datetime.now(UTC),
                )
            )

    def audit(self, scope: str, offset: int = 0) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    select(actions)
                    .where(actions.c.scope == scope)
                    .order_by(actions.c.created_at.desc(), actions.c.id)
                    .offset(offset)
                    .limit(50)
                ).mappings()
            ]

    def recover(self) -> None:
        """Single backend startup: projections still hold the last committed settings."""
        with self.engine.begin() as connection:
            connection.execute(
                update(actions)
                .where(actions.c.outcome == "pending")
                .values(
                    outcome="interrupted",
                    message="Server restarted before completion was recorded. "
                    "The last committed setup and saved run have been restored; check current status.",
                    finished_at=datetime.now(UTC),
                )
            )
            connection.execute(update(heads).values(pending_action=None))
