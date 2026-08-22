from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

from . import __version__
from .core import AnalysisError, analyze, discover_coverage_report, format_report, run_command

DEFAULT_COVERAGE = Path('target/coverage/coverage.json')
DEFAULT_TEST_COMMAND = 'swift test --enable-code-coverage --show-codecov-path'
SAFE_GENERATED_NAMES = {"target", "coverage", ".coverage", "build", ".build"}


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description='Calculate strict CRAP scores for Swift functions.')
    value.add_argument("filters", nargs="*", help="Only analyze source paths that contain one of these fragments.")
    value.add_argument("--root", type=Path, default=Path("."), help="Project root.")
    value.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE, help="LCOV, Cobertura, Istanbul, coverage.py, or LLVM JSON report.")
    value.add_argument("--test-command", default=DEFAULT_TEST_COMMAND, help="Command that runs tests and creates coverage.")
    value.add_argument("--timeout", type=float, default=1800.0, help="Test-command timeout in seconds.")
    value.add_argument("--no-test", action="store_true", help="Read an existing coverage report without running tests.")
    value.add_argument("--allow-missing-coverage", action="store_true", help="Permit functions without matched coverage. Disabled by default.")
    value.add_argument("--allow-empty", action="store_true", help="Permit a project with no discovered functions.")
    value.add_argument("--allow-parse-errors", action="store_true", help="Analyze partial syntax trees. Disabled by default.")
    value.add_argument("--include-tests", action="store_true", help="Include test sources in analysis.")
    value.add_argument("--json", action="store_true", dest="json_output", help="Write the versioned JSON result format.")
    value.add_argument("--fail-over", type=float, default=None, metavar="SCORE", help="Exit 2 when any CRAP score is above SCORE.")
    value.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return value


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _safe_generated(path: Path, root: Path) -> bool:
    if not _inside(root, path):
        return False
    relative = path.resolve().relative_to(root.resolve())
    return bool(relative.parts) and relative.parts[0] in SAFE_GENERATED_NAMES


def _fingerprint(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prepare_output(path: Path, root: Path) -> tuple[str | None, float]:
    before = _fingerprint(path)
    started = time.time()
    if path.exists() and _safe_generated(path, root):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    if path.suffix:
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        path.mkdir(parents=True, exist_ok=True)
    return before, started


def _resolve_swift_report(command_output: str, requested: Path) -> Path:
    if requested.exists():
        return discover_coverage_report(requested)
    candidates: list[Path] = []
    for token in command_output.replace("\n", " ").split():
        cleaned = token.strip("'\"[](),")
        if cleaned.endswith(".json"):
            candidate = Path(cleaned).expanduser()
            if candidate.exists():
                candidates.append(candidate)
    if candidates:
        return candidates[-1]
    return discover_coverage_report(requested)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    root = args.root.resolve()
    coverage = args.coverage if args.coverage.is_absolute() else root / args.coverage
    try:
        if not args.no_test:
            if not args.test_command:
                raise AnalysisError("--test-command is required for this language unless --no-test is used")
            before, started = _prepare_output(coverage, root)
            completed = run_command(args.test_command, root, args.timeout)
            report = _resolve_swift_report(completed.stdout or "", coverage) if 'swift' == "swift" else discover_coverage_report(coverage)
            if report.stat().st_mtime + 1 < started:
                raise AnalysisError(f"coverage report is stale: {report}")
            if before is not None and not _safe_generated(coverage, root) and _fingerprint(report) == before:
                raise AnalysisError(f"coverage report was not updated by the test command: {report}")
            coverage = report
        else:
            coverage = discover_coverage_report(coverage)

        metrics = analyze(root, coverage, args.filters, args.include_tests, args.allow_parse_errors)
        if not metrics and not args.allow_empty:
            raise AnalysisError("no functions were discovered")
        missing = [metric for metric in metrics if metric.coverage is None]
        if missing and not args.allow_missing_coverage:
            sample = ", ".join(f"{item.file}:{item.start_line} {item.name}" for item in missing[:5])
            raise AnalysisError(f"coverage is missing for {len(missing)} function(s): {sample}")
    except (OSError, ValueError, AnalysisError, json.JSONDecodeError) as error:
        print(f"crap4swift: {error}", file=sys.stderr)
        return 1

    over = [metric for metric in metrics if args.fail_over is not None and metric.crap is not None and metric.crap > args.fail_over]
    if args.json_output:
        payload = {
            "schema_version": 1,
            "tool": 'crap4swift',
            "version": __version__,
            "root": root.as_posix(),
            "summary": {
                "functions": len(metrics),
                "missing_coverage": sum(metric.coverage is None for metric in metrics),
                "over_limit": len(over),
                "limit": args.fail_over,
            },
            "functions": [metric.to_dict() for metric in metrics],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_report(metrics), end="")
    return 2 if over else 0


if __name__ == "__main__":
    raise SystemExit(main())
