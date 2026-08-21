from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import __version__
from .core import analyze, format_report, run_test_command

DEFAULT_COVERAGE = Path('target/coverage/coverage.json')
DEFAULT_TEST_COMMAND = 'swift test --enable-code-coverage'


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description='CRAP metric for Swift projects')
    value.add_argument("filters", nargs="*", help="Only analyze source paths that contain one of these fragments.")
    value.add_argument("--root", type=Path, default=Path("."), help="Project root.")
    value.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE, help="Coverage report path.")
    value.add_argument("--test-command", default=DEFAULT_TEST_COMMAND, help="Command that runs tests and creates the coverage report.")
    value.add_argument("--no-test", action="store_true", help="Do not run tests. Read an existing coverage report.")
    value.add_argument("--require-coverage", action="store_true", help="Fail when any function has no coverage data.")
    value.add_argument("--json", action="store_true", dest="json_output", help="Write JSON instead of a table.")
    value.add_argument("--fail-over", type=float, default=None, metavar="SCORE", help="Exit with status 2 when a CRAP score is above SCORE.")
    value.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    root = args.root.resolve()
    coverage_path = args.coverage if args.coverage.is_absolute() else root / args.coverage
    try:
        if not args.no_test:
            if coverage_path.parent.name == "coverage" and coverage_path.parent.parent.name == "target":
                shutil.rmtree(coverage_path.parent, ignore_errors=True)
            coverage_path.parent.mkdir(parents=True, exist_ok=True)
            run_test_command(args.test_command, root)
        metrics = analyze(root, coverage_path if coverage_path.exists() else None, args.filters)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"crap4swift: {error}", file=sys.stderr)
        return 1

    if args.json_output:
        print(json.dumps([metric.to_dict() for metric in metrics], indent=2, sort_keys=True))
    else:
        print(format_report(metrics), end="")

    if args.require_coverage and any(metric.coverage is None for metric in metrics):
        return 2
    if args.fail_over is not None and any(metric.crap is not None and metric.crap > args.fail_over for metric in metrics):
        return 2
    return 0
