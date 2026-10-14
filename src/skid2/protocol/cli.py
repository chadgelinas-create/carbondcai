"""Command line interface for protocol review, imports, generation, and drift."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from skid2.protocol.generation import (
    GenerationError,
    check_drift,
    render_artifacts,
    write_artifacts,
)
from skid2.protocol.importer import ADAPTERS, stage_workbook
from skid2.protocol.validation import validate_paths


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skid2-protocol")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate")
    validate.add_argument("paths", nargs="+", type=Path)
    validate.add_argument(
        "--profile", choices=("candidate", "reference", "production"), default="candidate"
    )
    validate.add_argument("--format", choices=("human", "json"), default="human")

    generate = commands.add_parser("generate")
    generate.add_argument("--input-root", type=Path, default=Path("protocol"))
    generate.add_argument(
        "--schema", type=Path, default=Path("protocol/schema/protocol-v1.schema.json")
    )
    generate.add_argument("--output-root", type=Path, default=Path("generated/protocol"))
    generate.add_argument("--profile", choices=("review", "production"), default="review")

    drift = commands.add_parser("check-drift")
    drift.add_argument("--input-root", type=Path, default=Path("protocol"))
    drift.add_argument(
        "--schema", type=Path, default=Path("protocol/schema/protocol-v1.schema.json")
    )
    drift.add_argument("--output-root", type=Path, default=Path("generated/protocol"))

    importer = commands.add_parser("import")
    importer.add_argument("workbook", type=Path)
    importer.add_argument("--adapter", choices=tuple(sorted(ADAPTERS)), required=True)
    importer.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one bounded protocol command without ordinary stack traces."""

    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "validate":
            report = validate_paths(arguments.paths, arguments.profile)
            if arguments.format == "json":
                print(report.as_json())
            else:
                for diagnostic in report.diagnostics:
                    print(
                        f"{diagnostic.source}:{diagnostic.path}: "
                        f"{diagnostic.severity} {diagnostic.code}: {diagnostic.message}"
                    )
                print(
                    f"{'accepted' if report.accepted else 'rejected'}: "
                    f"{len(report.documents)} document(s), "
                    f"{len(report.diagnostics)} diagnostic(s)"
                )
            return 0 if report.accepted else 1

        if arguments.command in {"generate", "check-drift"}:
            profile = arguments.profile if arguments.command == "generate" else "review"
            artifacts = render_artifacts(arguments.input_root, arguments.schema, profile)
            if arguments.command == "generate":
                write_artifacts(artifacts, arguments.output_root)
                print(f"generated {len(artifacts)} deterministic protocol artifacts")
                return 0
            diagnostics = check_drift(artifacts, arguments.output_root)
            if diagnostics:
                print("\n".join(diagnostics))
                return 1
            print(f"protocol drift check passed for {len(artifacts)} artifacts")
            return 0

        if arguments.command == "import":
            result = stage_workbook(arguments.workbook, ADAPTERS[arguments.adapter])
            if arguments.output is not None:
                arguments.output.write_text(result.staging_json() + "\n", encoding="utf-8")
            print(json.dumps(result.safe_summary(), indent=2, sort_keys=True))
            return 0
    except (GenerationError, OSError, ValueError) as exc:
        print(f"protocol command failed: {exc}")
        return 1
    raise RuntimeError("unreachable protocol command")
