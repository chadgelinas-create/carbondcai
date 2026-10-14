"""Backend checks and account setup for the reusable Python image."""

from __future__ import annotations

import argparse
import getpass
import json
from collections.abc import Sequence

from skid2.persistence.accounts import Accounts
from skid2.persistence.database import build_engine, database_status
from skid2.settings import get_settings


def build_parser() -> argparse.ArgumentParser:
    """Create the backend command parser."""

    parser = argparse.ArgumentParser(prog="skid2-backend")
    parser.add_argument(
        "command",
        choices=(
            "check-settings",
            "check-database",
            "show-revision",
            "import-smoke",
            "create-user",
        ),
    )
    return parser


def run(command: str) -> int:
    """Execute one backend check and return a process status."""

    if command == "create-user":
        settings = get_settings()
        username = input("Username: ").strip()
        role = input("Role (viewer/operator/admin): ").strip()
        password = getpass.getpass("Password (at least 12 characters): ")
        if password != getpass.getpass("Confirm password: "):
            raise ValueError("Passwords do not match")
        engine = build_engine(settings)
        try:
            Accounts(engine, settings.session_seconds).create_user(username, password, role)
        finally:
            engine.dispose()
        print("User created")
        return 0

    if command == "check-settings":
        print(json.dumps(get_settings().redacted_summary(), sort_keys=True))
        return 0

    if command in {"check-database", "show-revision"}:
        current = database_status(get_settings())
        if command == "show-revision":
            print(current.revision)
        else:
            print(
                json.dumps(
                    {
                        "database": "ready",
                        "revision": current.revision,
                        "timescaledb": current.timescaledb_version,
                    },
                    sort_keys=True,
                )
            )
        return 0

    if command == "import-smoke":
        import skid2.application
        import skid2.backend
        import skid2.contracts
        import skid2.persistence
        import skid2.protocol
        import skid2.reference
        import skid2.sim

        package_count = len(
            (
                skid2.application,
                skid2.backend,
                skid2.contracts,
                skid2.persistence,
                skid2.protocol,
                skid2.reference,
                skid2.sim,
            )
        )
        print(f"imported {package_count} package boundaries")
        return 0

    raise ValueError("unsupported backend command")


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point."""

    arguments = build_parser().parse_args(argv)
    return run(arguments.command)


if __name__ == "__main__":
    raise SystemExit(main())
