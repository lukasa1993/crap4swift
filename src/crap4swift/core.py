from __future__ import annotations

import json
import math
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from tree_sitter_language_pack import get_parser

LANGUAGE = 'swift'
PARSER_BY_EXTENSION = {}
DISPLAY_LANGUAGE = 'Swift'
EXTENSIONS = ('.swift',)
FUNCTION_TYPES = frozenset(('function_declaration', 'init_declaration', 'deinit_declaration', 'subscript_declaration'))
EXCLUDED_DIRS = frozenset(('.git', '.hg', '.idea', '.pytest_cache', '.tox', '.venv', '.build', 'build', 'coverage', 'dist', 'node_modules', 'target', 'vendor', 'venv', 'DerivedData', 'Pods'))
TEST_DIRS = frozenset(('Tests', 'tests'))
TEST_SUFFIXES = ('Tests.swift', 'Test.swift')

DECISION_TYPES: dict[str, frozenset[str]] = {
    "typescript": frozenset({
        "if_statement", "for_statement", "for_in_statement", "while_statement", "do_statement",
        "switch_case", "catch_clause", "ternary_expression", "conditional_type",
    }),
    "python": frozenset({
        "if_statement", "elif_clause", "for_statement", "while_statement", "except_clause",
        "case_clause", "conditional_expression", "list_comprehension", "set_comprehension",
        "dictionary_comprehension", "generator_expression",
    }),
    "rust": frozenset({
        "if_expression", "for_expression", "while_expression", "loop_expression",
        "match_arm", "catch_clause", "conditional_expression",
    }),
    "swift": frozenset({
        "if_statement", "guard_statement", "for_statement", "while_statement",
        "repeat_while_statement", "switch_entry", "catch_clause", "ternary_expression",
    }),
    "objc": frozenset({
        "if_statement", "for_statement", "while_statement", "do_statement",
        "case_statement", "conditional_expression", "catch_clause",
    }),
    "bash": frozenset({
        "if_statement", "elif_clause", "for_statement", "c_style_for_statement",
        "while_statement", "case_item", "conditional_expression",
    }),
    "c": frozenset({
        "if_statement", "for_statement", "while_statement", "do_statement",
        "case_statement", "conditional_expression",
    }),
    "cpp": frozenset({
        "if_statement", "for_statement", "while_statement", "do_statement",
        "case_statement", "conditional_expression", "catch_clause",
    }),
}

BINARY_TYPES = frozenset({
    "binary_expression", "logical_expression", "boolean_expression", "test_expression",
    "arithmetic_expression", "compound_expression", "list", "binary_operator", "boolean_operator",
})
NAME_TYPES = frozenset({
    "identifier", "field_identifier", "type_identifier", "simple_identifier", "word",
    "operator_name", "destructor_name", "qualified_identifier", "scoped_identifier",
    "function_identifier", "method_selector", "keyword_selector",
})


class AnalysisError(RuntimeError):
    pass


@dataclass(frozen=True)
class FunctionMetric:
    name: str
    file: str
    start_line: int
    end_line: int
    complexity: int
    coverage: float | None
    crap: float | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class CoverageData:
    lines: dict[str, dict[int, int]]


def normalize_path(value: str) -> str:
    return value.replace("\\", "/").removeprefix("./")


def _point_row(point: Any) -> int:
    if hasattr(point, "row"):
        return int(point.row)
    return int(point[0])


def _node_text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _walk(node: Any) -> Iterator[Any]:
    yield node
    for child in node.children:
        yield from _walk(child)


def _is_test_path(relative: str) -> bool:
    path = Path(relative)
    if any(part in TEST_DIRS or part.lower() in {value.lower() for value in TEST_DIRS} for part in path.parts):
        return True
    return path.name.endswith(TEST_SUFFIXES)


def discover_files(root: Path, filters: Sequence[str] = (), include_tests: bool = False) -> list[Path]:
    files: list[Path] = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in EXCLUDED_DIRS)
        for filename in sorted(filenames):
            if not filename.endswith(EXTENSIONS):
                continue
            path = Path(directory, filename)
            relative = path.relative_to(root).as_posix()
            if not include_tests and _is_test_path(relative):
                continue
            if filters and not any(fragment in relative for fragment in filters):
                continue
            files.append(path)
    return files


def parser_for_path(path: Path) -> Any:
    return get_parser(PARSER_BY_EXTENSION.get(path.suffix.lower(), LANGUAGE))


def parse_source(path: Path, allow_parse_errors: bool = False) -> tuple[bytes, Any]:
    source = path.read_bytes()
    tree = parser_for_path(path).parse(source)
    if tree.root_node.has_error and not allow_parse_errors:
        errors: list[str] = []
        for node in _walk(tree.root_node):
            if node.type == "ERROR" or getattr(node, "is_missing", False):
                line = _point_row(node.start_point) + 1
                errors.append(f"line {line}: {node.type}")
                if len(errors) >= 5:
                    break
        details = ", ".join(errors) or "unknown parse error"
        raise AnalysisError(f"{path} contains syntax-tree errors ({details})")
    return source, tree.root_node


def _first_name_node(node: Any) -> Any | None:
    direct = node.child_by_field_name("name")
    if direct is not None:
        return direct
    declarator = node.child_by_field_name("declarator")
    if declarator is not None:
        candidates = [child for child in _walk(declarator) if child.type in NAME_TYPES]
        if candidates:
            return candidates[-1]
    selector = node.child_by_field_name("selector")
    if selector is not None:
        return selector
    candidates = [child for child in _walk(node) if child.type in NAME_TYPES]
    return candidates[0] if candidates else None


def _objective_c_selector(node: Any, source: bytes) -> str | None:
    signature = _node_text(node, source).split("{", 1)[0]
    parts = re.findall(r"([A-Za-z_]\w*)\s*:", signature)
    if parts:
        return ":".join(parts) + ":"
    match = re.search(r"[-+]\s*\([^)]*\)\s*([A-Za-z_]\w*)", signature)
    return match.group(1) if match else None


def _qualified_owner(node: Any, source: bytes) -> str | None:
    parent = getattr(node, "parent", None)
    while parent is not None:
        if LANGUAGE == "rust" and parent.type == "impl_item":
            target = parent.child_by_field_name("type") or parent.child_by_field_name("trait")
            if target is not None:
                return _node_text(target, source).strip()
        if LANGUAGE == "typescript" and parent.type in {"class_declaration", "interface_declaration"}:
            name = parent.child_by_field_name("name")
            if name is not None:
                return _node_text(name, source).strip()
        if LANGUAGE == "swift" and parent.type in {
            "class_declaration", "struct_declaration", "enum_declaration", "actor_declaration", "extension_declaration"
        }:
            name = parent.child_by_field_name("name")
            if name is not None:
                return _node_text(name, source).strip()
        parent = getattr(parent, "parent", None)
    return None


def function_name(node: Any, source: bytes) -> str:
    line = _point_row(node.start_point) + 1
    if node.type == "lambda_expression":
        return f"<lambda@{line}>"
    if LANGUAGE == "typescript" and node.type in {"arrow_function", "function_expression"}:
        parent = getattr(node, "parent", None)
        if parent is not None:
            parent_name = parent.child_by_field_name("name")
            if parent_name is not None:
                return _node_text(parent_name, source).strip()
    if LANGUAGE == "objc" and node.type == "method_definition":
        selector = _objective_c_selector(node, source)
        if selector:
            return selector
    if LANGUAGE == "swift" and node.type in {"init_declaration", "deinit_declaration", "subscript_declaration"}:
        base = {"init_declaration": "init", "deinit_declaration": "deinit", "subscript_declaration": "subscript"}[node.type]
        owner = _qualified_owner(node, source)
        return f"{owner}.{base}" if owner else base
    name_node = _first_name_node(node)
    name = _node_text(name_node, source).strip() if name_node is not None else f"<function@{line}>"
    owner = _qualified_owner(node, source)
    if owner and not name.startswith(owner):
        separator = "::" if LANGUAGE == "rust" else "."
        return f"{owner}{separator}{name}"
    return name


def _operator_count(node: Any, source: bytes) -> int:
    # Tree-sitter grammars use different parent node names for logical
    # expressions. Count direct && and || operator children once.
    count = 0
    for child in node.children:
        value = _node_text(child, source).strip()
        if value in {"&&", "||", "and", "or"}:
            count += 1
    return count


def complexity(function_node: Any, source: bytes) -> int:
    decisions = DECISION_TYPES[LANGUAGE]

    def visit(node: Any, root: bool = False) -> int:
        if not root and node.type in FUNCTION_TYPES:
            return 0
        value = 1 if node.type in decisions else 0
        value += _operator_count(node, source)
        for child in node.children:
            value += visit(child)
        return value

    return 1 + sum(visit(child) for child in function_node.children)


def extract_functions(path: Path, root: Path, allow_parse_errors: bool = False) -> list[FunctionMetric]:
    source, root_node = parse_source(path, allow_parse_errors)
    metrics: list[FunctionMetric] = []
    for node in _walk(root_node):
        if node.type not in FUNCTION_TYPES:
            continue
        parent = getattr(node, "parent", None)
        if parent is not None and parent.type in FUNCTION_TYPES:
            continue
        metrics.append(
            FunctionMetric(
                name=function_name(node, source),
                file=path.relative_to(root).as_posix(),
                start_line=_point_row(node.start_point) + 1,
                end_line=_point_row(node.end_point) + 1,
                complexity=complexity(node, source),
                coverage=None,
                crap=None,
            )
        )
    if LANGUAGE == "bash":
        function_ranges = [(metric.start_line, metric.end_line) for metric in metrics]
        top_level = [child for child in root_node.named_children if child.type not in FUNCTION_TYPES]
        if top_level:
            start = min(_point_row(child.start_point) + 1 for child in top_level)
            end = max(_point_row(child.end_point) + 1 for child in top_level)
            metrics.append(
                FunctionMetric(
                    name="<script>", file=path.relative_to(root).as_posix(),
                    start_line=start, end_line=end,
                    complexity=complexity(root_node, source), coverage=None, crap=None,
                )
            )
    metrics.sort(key=lambda item: (item.start_line, item.name))
    return metrics


def _merge_line(target: dict[int, int], line: int, count: int) -> None:
    if line > 0:
        target[line] = max(target.get(line, 0), count)


def _load_lcov(text: str) -> dict[str, dict[int, int]]:
    result: dict[str, dict[int, int]] = {}
    current: str | None = None
    for raw in text.splitlines():
        if raw.startswith("SF:"):
            current = normalize_path(raw[3:].strip())
            result.setdefault(current, {})
        elif current and raw.startswith("DA:"):
            fields = raw[3:].split(",")
            if len(fields) >= 2:
                _merge_line(result[current], int(fields[0]), int(float(fields[1])))
    return result


def _load_cobertura(path: Path) -> dict[str, dict[int, int]]:
    result: dict[str, dict[int, int]] = {}
    root = ET.parse(path).getroot()
    for class_node in root.findall(".//class"):
        filename = class_node.attrib.get("filename")
        if not filename:
            continue
        lines = result.setdefault(normalize_path(filename), {})
        for line_node in class_node.findall("./lines/line"):
            _merge_line(lines, int(line_node.attrib.get("number", "0")), int(float(line_node.attrib.get("hits", "0"))))
    return result


def _load_json(path: Path) -> dict[str, dict[int, int]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AnalysisError("coverage JSON must contain an object")
    result: dict[str, dict[int, int]] = {}

    files = payload.get("files")
    if isinstance(files, dict):
        for filename, raw in files.items():
            if not isinstance(raw, dict):
                continue
            executed = {int(value) for value in raw.get("executed_lines", [])}
            missing = {int(value) for value in raw.get("missing_lines", [])}
            if executed or missing:
                lines = result.setdefault(normalize_path(str(filename)), {})
                for line in executed | missing:
                    _merge_line(lines, line, 1 if line in executed else 0)
        if result:
            return result

    for filename, raw in payload.items():
        if not isinstance(raw, dict) or not isinstance(raw.get("statementMap"), dict) or not isinstance(raw.get("s"), dict):
            continue
        lines = result.setdefault(normalize_path(str(filename)), {})
        for key, location in raw["statementMap"].items():
            if not isinstance(location, dict):
                continue
            start = location.get("start")
            if isinstance(start, dict):
                _merge_line(lines, int(start.get("line", 0)), int(raw["s"].get(key, 0)))
    if result:
        return result

    data = payload.get("data")
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            functions = item.get("functions")
            if not isinstance(functions, list):
                continue
            for function in functions:
                if not isinstance(function, dict):
                    continue
                filenames = function.get("filenames")
                regions = function.get("regions")
                if not isinstance(filenames, list) or not isinstance(regions, list):
                    continue
                for region in regions:
                    if not isinstance(region, list) or len(region) < 6:
                        continue
                    kind = int(region[7]) if len(region) > 7 else 0
                    if kind != 0:
                        continue
                    start_line, end_line, count, file_index = int(region[0]), int(region[2]), int(region[4]), int(region[5])
                    if not 0 <= file_index < len(filenames):
                        continue
                    lines = result.setdefault(normalize_path(str(filenames[file_index])), {})
                    for line in range(start_line, end_line + 1):
                        _merge_line(lines, line, count)
    return result


def discover_coverage_report(path: Path) -> Path:
    if path.is_file():
        return path
    if not path.exists():
        raise AnalysisError(f"coverage report does not exist: {path}")
    candidates: list[Path] = []
    for name in ("lcov.info", "coverage-final.json", "coverage.json", "cobertura.xml"):
        candidates.extend(path.rglob(name))
    candidates = sorted({candidate.resolve() for candidate in candidates}, key=lambda value: (len(value.parts), value.as_posix()))
    if not candidates:
        raise AnalysisError(f"no supported coverage report found under {path}")
    return candidates[0]


def load_coverage(path: Path) -> CoverageData:
    report = discover_coverage_report(path)
    if report.suffix.lower() == ".xml":
        lines = _load_cobertura(report)
    else:
        text = report.read_text(encoding="utf-8", errors="replace")
        lines = _load_lcov(text) if text.lstrip().startswith(("TN:", "SF:")) or report.suffix.lower() == ".info" else _load_json(report)
    if not lines:
        raise AnalysisError(f"coverage report contains no executable lines: {report}")
    return CoverageData(lines)


def _coverage_for_file(coverage: CoverageData, root: Path, filename: str) -> dict[int, int] | None:
    normalized = normalize_path(filename)
    absolute = normalize_path(str((root / filename).resolve()))
    if normalized in coverage.lines:
        return coverage.lines[normalized]
    if absolute in coverage.lines:
        return coverage.lines[absolute]
    suffix_matches = [value for key, value in coverage.lines.items() if key.endswith("/" + normalized) or normalized.endswith("/" + key)]
    if len(suffix_matches) == 1:
        return suffix_matches[0]
    basename_matches = [value for key, value in coverage.lines.items() if Path(key).name == Path(normalized).name]
    return basename_matches[0] if len(basename_matches) == 1 else None


def score(complexity_value: int, coverage_percent: float) -> float:
    uncovered = 1.0 - coverage_percent / 100.0
    return complexity_value * complexity_value * uncovered**3 + complexity_value


def apply_coverage(root: Path, metrics: Iterable[FunctionMetric], coverage: CoverageData) -> list[FunctionMetric]:
    output: list[FunctionMetric] = []
    for metric in metrics:
        line_counts = _coverage_for_file(coverage, root, metric.file)
        if line_counts is None:
            output.append(metric)
            continue
        relevant = {line: count for line, count in line_counts.items() if metric.start_line <= line <= metric.end_line}
        if not relevant:
            output.append(metric)
            continue
        percent = 100.0 * sum(count > 0 for count in relevant.values()) / len(relevant)
        output.append(FunctionMetric(**{**metric.to_dict(), "coverage": percent, "crap": score(metric.complexity, percent)}))
    return output


def analyze(
    root: Path,
    coverage_path: Path | None,
    filters: Sequence[str] = (),
    include_tests: bool = False,
    allow_parse_errors: bool = False,
) -> list[FunctionMetric]:
    metrics: list[FunctionMetric] = []
    for path in discover_files(root, filters, include_tests):
        metrics.extend(extract_functions(path, root, allow_parse_errors))
    if coverage_path is not None:
        metrics = apply_coverage(root, metrics, load_coverage(coverage_path))
    return sorted(metrics, key=lambda item: (item.crap is None, -(item.crap or -math.inf), item.file, item.start_line, item.name))


def run_command(command: str, root: Path, timeout_seconds: float | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=root,
        shell=True,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout_seconds,
    )
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.returncode != 0:
        raise AnalysisError(f"command failed with exit code {completed.returncode}: {command}")
    return completed


def format_report(metrics: Sequence[FunctionMetric]) -> str:
    header = f"{'Function':38} {'File':44} {'CC':>4} {'Cov%':>7} {'CRAP':>8}"
    lines = ["CRAP Report", "===========", header, "-" * len(header)]
    for metric in metrics:
        coverage = "N/A" if metric.coverage is None else f"{metric.coverage:.1f}%"
        crap = "N/A" if metric.crap is None else f"{metric.crap:.2f}"
        lines.append(f"{metric.name[:38]:38} {metric.file[:44]:44} {metric.complexity:4d} {coverage:>7} {crap:>8}")
    return "\n".join(lines) + "\n"
