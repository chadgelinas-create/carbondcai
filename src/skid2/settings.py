"""Validated deployment settings shared by Python service entry points."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

EnvironmentName = Literal["development", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
DataSource = Literal["sim", "live"]
PUBLIC_UI_ORIGINS = (
    "https://carbon-dc-ems-ui-demo.netlify.app",
    "https://carbon-dc-ems-ui.netlify.app",
    "https://carbondcai-ems.pages.dev",
)


class Settings(BaseSettings):
    """Process settings loaded exclusively through the `SKID2_` namespace."""

    model_config = SettingsConfigDict(
        env_prefix="SKID2_",
        case_sensitive=False,
        extra="ignore",
        env_file_encoding="utf-8",
    )

    environment: EnvironmentName
    service_name: str = Field(min_length=1, max_length=64)
    service_host: str = Field(min_length=1, max_length=255)
    service_port: int = Field(ge=1, le=65535)
    monitor_enabled: bool = True
    auth_enabled: bool = True
    browser_origin: str = "http://localhost:5173"
    secure_cookies: bool = False
    session_seconds: int = Field(default=28800, ge=60, le=86400)
    live_bess_map: Path | None = None
    simulation_enabled: bool = False
    simulation_step_seconds: float = Field(default=1.0, gt=0, le=60)
    simulation_profile: Literal["stage0"] = "stage0"
    simulation_seed: int | None = Field(default=None, ge=0, le=2147483647)
    protocol_root: Path = Path("protocol")
    modbus_enabled: bool = False
    modbus_bind_host: str = Field(default="127.0.0.1", min_length=1, max_length=255)
    modbus_client_host: str = Field(default="127.0.0.1", min_length=1, max_length=255)
    modbus_port: int = Field(default=1502, ge=1, le=65535)
    modbus_ba1_id: int = Field(default=1, ge=1, le=247)
    modbus_cnvtr1_id: int = Field(default=2, ge=1, le=247)
    modbus_cnvtr2_id: int = Field(default=3, ge=1, le=247)
    modbus_dc600_id: int = Field(default=4, ge=1, le=247)
    modbus_st4_id: int = Field(default=5, ge=1, le=247)
    modbus_st5_id: int = Field(default=6, ge=1, le=247)
    modbus_st6_id: int = Field(default=7, ge=1, le=247)
    modbus_bank_path: Path = Path("generated/protocol/simulator/register_banks.json")
    log_level: LogLevel = "INFO"
    database_url: SecretStr
    synthetic_enabled: bool = False
    synthetic_interval_seconds: float = Field(default=1.0, ge=0.1, le=3600.0)
    freshness_threshold_seconds: float = Field(default=3.0, ge=0.2, le=86400.0)
    synthetic_max_samples: int = Field(default=0, ge=0, le=1_000_000)
    data_source: DataSource = "sim"
    ems_enabled: bool = True
    ems_poll_seconds: float = Field(default=1.0, gt=0.1, le=60.0)
    auth_secret: SecretStr = SecretStr("local-development-auth-secret")
    # Comma-separated extra browser origins allowed by CORS (e.g. Netlify demos).
    cors_origins: str = ""
    public_app_url: str = ""
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = "Skid 2 EMS <noreply@carbondc.io>"
    smtp_starttls: bool = True
    mail_dir: Path = Path("data/mail")

    @field_validator("live_bess_map", mode="before")
    @classmethod
    def empty_live_map(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("browser_origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
        ):
            raise ValueError("browser origin must contain only scheme, host and optional port")
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        """Reject malformed or non-PostgreSQL URLs without echoing credentials."""

        parsed = urlsplit(value.get_secret_value())
        if parsed.scheme not in {"postgresql", "postgresql+psycopg"}:
            raise ValueError("database URL must use PostgreSQL with the psycopg driver")
        if not parsed.hostname or not parsed.path.strip("/"):
            raise ValueError("database URL must include a host and database name")
        return value

    @model_validator(mode="after")
    def protect_production(self) -> Self:
        """Prevent production from silently accepting the published local credential."""

        if self.environment == "production":
            if "local-development-only" in self.database_url.get_secret_value():
                raise ValueError("production settings contain a local-development credential")
            if "local-development-auth-secret" in self.auth_secret.get_secret_value():
                raise ValueError("production settings contain a local-development auth secret")
            if self.synthetic_enabled:
                raise ValueError("synthetic walking-skeleton source is forbidden in production")
            if self.simulation_enabled:
                raise ValueError("simulation controls are forbidden in production")
            if not self.auth_enabled or not self.secure_cookies:
                raise ValueError("production requires authentication and secure cookies")
            if not self.browser_origin.startswith("https://"):
                raise ValueError("production requires an HTTPS browser origin")
        if self.simulation_enabled and self.simulation_seed is None:
            raise ValueError("simulation requires an explicit seed")
        if self.modbus_enabled and not self.simulation_enabled:
            raise ValueError("Modbus simulation requires the Simulator Lab")
        modbus_ids = {
            self.modbus_ba1_id,
            self.modbus_cnvtr1_id,
            self.modbus_cnvtr2_id,
            self.modbus_dc600_id,
            self.modbus_st4_id,
            self.modbus_st5_id,
            self.modbus_st6_id,
        }
        if len(modbus_ids) != 7:
            raise ValueError("Modbus device IDs must be different")
        if self.freshness_threshold_seconds <= self.synthetic_interval_seconds:
            raise ValueError("freshness threshold must exceed the synthetic interval")
        return self

    def redacted_summary(self) -> dict[str, str | int]:
        """Return fields safe for startup diagnostics."""

        return {
            "environment": self.environment,
            "service_name": self.service_name,
            "service_host": self.service_host,
            "service_port": self.service_port,
            "log_level": self.log_level,
            "database_url": "**********",
            "simulation_enabled": str(self.simulation_enabled).lower(),
            "simulation_step_seconds": str(self.simulation_step_seconds),
            "simulation_profile": self.simulation_profile,
            "simulation_seed": str(self.simulation_seed),
            "protocol_root": str(self.protocol_root),
            "modbus_enabled": str(self.modbus_enabled).lower(),
            "modbus_bind_host": self.modbus_bind_host,
            "modbus_client_host": self.modbus_client_host,
            "modbus_port": self.modbus_port,
            "modbus_ba1_id": self.modbus_ba1_id,
            "modbus_cnvtr1_id": self.modbus_cnvtr1_id,
            "modbus_cnvtr2_id": self.modbus_cnvtr2_id,
            "modbus_dc600_id": self.modbus_dc600_id,
            "modbus_st4_id": self.modbus_st4_id,
            "modbus_st5_id": self.modbus_st5_id,
            "modbus_st6_id": self.modbus_st6_id,
            "modbus_bank_path": str(self.modbus_bank_path),
            "synthetic_enabled": str(self.synthetic_enabled).lower(),
            "synthetic_interval_seconds": str(self.synthetic_interval_seconds),
            "freshness_threshold_seconds": str(self.freshness_threshold_seconds),
            "synthetic_max_samples": self.synthetic_max_samples,
            "data_source": self.data_source,
            "ems_enabled": str(self.ems_enabled).lower(),
            "auth_secret": "**********",
        }

    def login_origins(self) -> frozenset[str]:
        """Origins allowed for cookie login, invites, and password-reset email links."""

        origins = {self.browser_origin, *PUBLIC_UI_ORIGINS}
        if self.public_app_url:
            origins.add(self.public_app_url.rstrip("/"))
        for origin in self.cors_origins.split(","):
            cleaned = origin.strip().rstrip("/")
            if cleaned:
                origins.add(cleaned)
        if self.environment == "development":
            parsed = urlsplit(self.browser_origin)
            if parsed.hostname in {"localhost", "127.0.0.1"}:
                other = "127.0.0.1" if parsed.hostname == "localhost" else "localhost"
                port = f":{parsed.port}" if parsed.port else ""
                origins.add(f"{parsed.scheme}://{other}{port}")
        return frozenset(origins)


def load_settings(env_file: str | Path | None = None) -> Settings:
    """Load settings, optionally from an explicit file for local tooling/tests."""

    if env_file is None:
        return Settings()
    return Settings(_env_file=env_file)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one validated settings object per process."""

    return load_settings()
