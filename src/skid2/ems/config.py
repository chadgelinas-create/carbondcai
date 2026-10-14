"""Validated EMS configuration ownership with durable audit rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from skid2.ems.groups import CONFIG_CATALOG, ConfigSpec, config_spec, group_config_specs
from skid2.persistence.metadata import config_audit, device_config


class ConfigError(ValueError):
    """Configuration validation or persistence failure."""


@dataclass(frozen=True, slots=True)
class ConfigValue:
    group_id: str
    key: str
    value: float | None
    value_text: str | None
    unit: str
    updated_at: datetime
    updated_by: str


@dataclass(frozen=True, slots=True)
class AuditRow:
    group_id: str
    key: str
    old_value: str | None
    new_value: str
    changed_by: str
    changed_at: datetime


class ConfigService:
    """App-owned setpoints for the web/sim phase."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._cache: dict[tuple[str, str], ConfigValue] = {}

    def ensure_defaults(self) -> None:
        for item in CONFIG_CATALOG:
            existing = self.get(item.group_id, item.key)
            if existing is not None:
                continue
            if item.text:
                self._write(
                    item,
                    value=None,
                    value_text=str(item.default),
                    changed_by="system",
                    record_audit=False,
                )
            else:
                self._write(
                    item,
                    value=float(item.default),
                    value_text=None,
                    changed_by="system",
                    record_audit=False,
                )

    def get(self, group_id: str, key: str) -> ConfigValue | None:
        cached = self._cache.get((group_id, key))
        if cached is not None:
            return cached
        try:
            with self._engine.connect() as connection:
                row = (
                    connection.execute(
                        select(device_config).where(
                            device_config.c.group_id == group_id,
                            device_config.c.key == key,
                        )
                    )
                    .mappings()
                    .first()
                )
        except SQLAlchemyError as error:
            raise ConfigError("could not read config") from error
        if row is None:
            return None
        value = ConfigValue(
            group_id=str(row["group_id"]),
            key=str(row["key"]),
            value=None if row["value"] is None else float(row["value"]),
            value_text=None if row["value_text"] is None else str(row["value_text"]),
            unit=str(row["unit"]),
            updated_at=row["updated_at"],
            updated_by=str(row["updated_by"]),
        )
        self._cache[(group_id, key)] = value
        return value

    def list_group(self, group_id: str) -> list[ConfigValue]:
        self.ensure_defaults()
        return [self.require(item.group_id, item.key) for item in group_config_specs(group_id)]

    def require(self, group_id: str, key: str) -> ConfigValue:
        value = self.get(group_id, key)
        if value is None:
            raise ConfigError(f"missing config {group_id}.{key}")
        return value

    def numeric(self, group_id: str, key: str) -> float:
        item = self.require(group_id, key)
        if item.value is None:
            raise ConfigError(f"config {group_id}.{key} is not numeric")
        return item.value

    def text(self, group_id: str, key: str) -> str:
        item = self.require(group_id, key)
        return item.value_text or ""

    def set_value(
        self,
        group_id: str,
        key: str,
        raw: float | str,
        changed_by: str,
    ) -> ConfigValue:
        spec = config_spec(group_id, key)
        previous = self.get(group_id, key)
        if spec.text:
            text_value = str(raw).strip()
            if not text_value or len(text_value) > 500:
                raise ConfigError("station message must be 1..500 characters")
            return self._write(
                spec,
                value=None,
                value_text=text_value,
                changed_by=changed_by,
                previous=previous,
            )
        try:
            number = float(raw)
        except (TypeError, ValueError) as error:
            raise ConfigError("value must be numeric") from error
        if spec.minimum is not None and number < spec.minimum:
            raise ConfigError(f"value below minimum {spec.minimum}")
        if spec.maximum is not None and number > spec.maximum:
            raise ConfigError(f"value above maximum {spec.maximum}")
        return self._write(
            spec,
            value=number,
            value_text=None,
            changed_by=changed_by,
            previous=previous,
        )

    def audit(self, group_id: str, limit: int = 50) -> list[AuditRow]:
        try:
            with self._engine.connect() as connection:
                rows = (
                    connection.execute(
                        select(config_audit)
                        .where(config_audit.c.group_id == group_id)
                        .order_by(config_audit.c.changed_at.desc(), config_audit.c.id.desc())
                        .limit(limit)
                    )
                    .mappings()
                    .all()
                )
        except SQLAlchemyError as error:
            raise ConfigError("could not read audit") from error
        return [
            AuditRow(
                group_id=str(row["group_id"]),
                key=str(row["key"]),
                old_value=None if row["old_value"] is None else str(row["old_value"]),
                new_value=str(row["new_value"]),
                changed_by=str(row["changed_by"]),
                changed_at=row["changed_at"],
            )
            for row in rows
        ]

    def _write(
        self,
        spec: ConfigSpec,
        *,
        value: float | None,
        value_text: str | None,
        changed_by: str,
        previous: ConfigValue | None = None,
        record_audit: bool = True,
    ) -> ConfigValue:
        now = datetime.now(tz=UTC)
        payload = {
            "group_id": spec.group_id,
            "key": spec.key,
            "value": value,
            "value_text": value_text,
            "unit": spec.unit,
            "updated_at": now,
            "updated_by": changed_by,
        }
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(device_config)
                    .values(**payload)
                    .on_conflict_do_update(
                        index_elements=["group_id", "key"],
                        set_={
                            "value": value,
                            "value_text": value_text,
                            "unit": spec.unit,
                            "updated_at": now,
                            "updated_by": changed_by,
                        },
                    )
                )
                if record_audit:
                    old = _format(previous)
                    new = value_text if value_text is not None else str(value)
                    connection.execute(
                        insert(config_audit).values(
                            group_id=spec.group_id,
                            key=spec.key,
                            old_value=old,
                            new_value=new,
                            changed_by=changed_by,
                            changed_at=now,
                        )
                    )
        except SQLAlchemyError as error:
            raise ConfigError("could not persist config") from error
        result = ConfigValue(
            group_id=spec.group_id,
            key=spec.key,
            value=value,
            value_text=value_text,
            unit=spec.unit,
            updated_at=now,
            updated_by=changed_by,
        )
        self._cache[(spec.group_id, spec.key)] = result
        return result


def _format(previous: ConfigValue | None) -> str | None:
    if previous is None:
        return None
    if previous.value_text is not None:
        return previous.value_text
    if previous.value is None:
        return None
    return str(previous.value)
