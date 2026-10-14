"""Save connection settings and their audit record together."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from skid2.persistence.metadata import app_config, audit


class ConfigConflictError(ValueError):
    """Another user has already changed these settings."""


class ConfigRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def load(self) -> dict[str, Any]:
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(app_config).where(app_config.c.key == "monitor"))
                .mappings()
                .first()
            )
        if row is None:
            return {"revision": 0, "active": None, "connections": {}}
        return {"revision": row["revision"], **row["value"]}

    def save(self, values: dict[str, Any], username: str) -> dict[str, Any]:
        values = dict(values)
        revision = values.pop("revision")
        key = f"{values['device_id']}:{values['mode']}"
        with self.engine.begin() as connection:
            connection.execute(
                pg_insert(app_config)
                .values(
                    key="monitor",
                    value={"active": None, "connections": {}},
                    revision=0,
                )
                .on_conflict_do_nothing(index_elements=["key"])
            )
            row = (
                connection.execute(
                    select(app_config).where(app_config.c.key == "monitor").with_for_update()
                )
                .mappings()
                .one()
            )
            if row["revision"] != revision:
                raise ConfigConflictError("Settings changed. Reload and try again.")
            before = row["value"]
            after = {"active": key, "connections": {**before["connections"], key: values}}
            connection.execute(
                update(app_config)
                .where(app_config.c.key == "monitor")
                .values(value=after, revision=revision + 1)
            )
            connection.execute(
                insert(audit).values(
                    username=username,
                    action="connection_settings",
                    before={"revision": revision, **before},
                    after={"revision": revision + 1, **after},
                )
            )
        return {"revision": revision + 1, **after}
