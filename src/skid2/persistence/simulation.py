"""Separate durable simulator settings from the manually saved run."""

from typing import Any, Literal, Protocol

from sqlalchemy import Engine, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from skid2.persistence.metadata import app_config

SimulationKey = Literal["simulation.settings", "simulation.checkpoint"]


class SimulationStoreError(RuntimeError):
    """A simulator database operation did not complete."""


class SimulationStore(Protocol):
    def load(self, key: SimulationKey) -> dict[str, Any] | None: ...

    def save(self, key: SimulationKey, value: dict[str, Any]) -> None: ...

    def delete_checkpoint(self) -> None: ...


class SimulationRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def load(self, key: SimulationKey) -> dict[str, Any] | None:
        try:
            with self.engine.connect() as connection:
                return connection.execute(
                    select(app_config.c.value).where(app_config.c.key == key)
                ).scalar_one_or_none()
        except SQLAlchemyError as error:
            raise SimulationStoreError("Saved simulation data could not be loaded.") from error

    def save(self, key: SimulationKey, value: dict[str, Any]) -> None:
        statement = insert(app_config).values(key=key, value=value, revision=1)
        statement = statement.on_conflict_do_update(
            index_elements=[app_config.c.key],
            set_={"value": statement.excluded.value, "revision": app_config.c.revision + 1},
        )
        try:
            with self.engine.begin() as connection:
                connection.execute(statement)
        except SQLAlchemyError as error:
            raise SimulationStoreError(
                "Simulation data could not be saved. The previous save is unchanged."
            ) from error

    def delete_checkpoint(self) -> None:
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    delete(app_config).where(app_config.c.key == "simulation.checkpoint")
                )
        except SQLAlchemyError as error:
            raise SimulationStoreError("The saved run could not be deleted.") from error

    def clear_saved_simulation(self) -> None:
        """Drop durable settings and checkpoint so a fresh lab run can start."""

        try:
            with self.engine.begin() as connection:
                connection.execute(
                    delete(app_config).where(
                        app_config.c.key.in_(
                            ("simulation.settings", "simulation.checkpoint")
                        )
                    )
                )
        except SQLAlchemyError as error:
            raise SimulationStoreError("Saved simulation data could not be cleared.") from error
