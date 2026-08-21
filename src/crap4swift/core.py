from __future__ import annotations

import json
import math
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

LANGUAGE = 'swift'
EXTENSIONS = tuple(['.swift'])
EXCLUDED_DIRS = {".git", ".hg", ".idea", ".pytest_cache", ".tox", ".venv", "build", "coverage", "dist", "node_modules", "target", "vendor", "venv", ".build"}


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
class Segment:
    start_line: int
    end_line: int
    count: int


def normalize_path(value: str) -> str:
    return value.replace("\\", "/").removeprefix("./")


def _is_test_path(relative: str) -> bool:
    lowered = relative.lower()
    name = Path(relative).name.lower()
    if LANGUAGE == "typescript":
        return name.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx", ".d.ts")) or "/test/" in f"/{lowered}/" or "/tests/" in f"/{lowered}/"
    if LANGUAGE == "rust":
        return "/tests/" in f"/{lowered}/" or name.endswith("_test.rs")
    if LANGUAGE == "swift":
        return "/tests/" in f"/{lowered}/" or name.endswith("tests.swift")
    if LANGUAGE == "objective-c":
        return "/tests/" in f"/{lowered}/" or name.endswith(("tests.m", "tests.mm", "test.m", "test.mm"))
    if LANGUAGE == "bash":
        return "/test/" in f"/{lowered}/" or "/tests/" in f"/{lowered}/" or name.endswith(".bats")
    return False


def discover_files(root: Path, filters: Sequence[str] = ()) -> list[Path]:
    files: list[Path] = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in EXCLUDED_DIRS)
        for filename in sorted(filenames):
            if not filename.endswith(EXTENSIONS):
                continue
            path = Path(directory, filename)
            relative = path.relative_to(root).as_posix()
            if _is_test_path(relative):
                continue
            if filters and not any(fragment in relative for fragment in filters):
                continue
            files.append(path)
    return files


def mask_non_code(text: str) -> str:
    out = list(text)
    index = 0
    state = "code"
    quote = ""
    block_depth = 0
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if state == "code":
            if LANGUAGE == "bash" and char == "#":
                state = "line-comment"
                out[index] = " "
            elif LANGUAGE != "bash" and char == "/" and next_char == "/":
                state = "line-comment"
                out[index] = out[index + 1] = " "
                index += 1
            elif LANGUAGE != "bash" and char == "/" and next_char == "*":
                state = "block-comment"
                block_depth = 1
                out[index] = out[index + 1] = " "
                index += 1
            elif char in {'"', "'", "`"} and (LANGUAGE == "typescript" or char != "`"): 
                state = "string"
                quote = char
                out[index] = " "
            else:
                out[index] = char
        elif state == "line-comment":
            if char == "\n":
                state = "code"
                out[index] = "\n"
            else:
                out[index] = " "
        elif state == "block-comment":
            if char == "\n":
                out[index] = "\n"
            else:
                out[index] = " "
            if char == "/" and next_char == "*" and LANGUAGE == "rust":
                block_depth += 1
                out[index + 1] = " "
                index += 1
            elif char == "*" and next_char == "/":
                block_depth -= 1
                out[index + 1] = " "
                index += 1
                if block_depth == 0:
                    state = "code"
        else:
            if char == "\n":
                out[index] = "\n"
            else:
                out[index] = " "
            if char == "\\" and quote != "'" and index + 1 < len(text):
                if text[index + 1] != "\n":
                    out[index + 1] = " "
                index += 1
            elif char == quote:
                state = "code"
        index += 1
    return "".join(out)


def _matching_brace(masked: str, opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(masked)):
        if masked[index] == "{":
            depth += 1
        elif masked[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _line_at(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _type_ranges(masked: str) -> list[tuple[int, int, str]]:
    patterns: list[str]
    if LANGUAGE == "typescript":
        patterns = [r"\bclass\s+([A-Za-z_$][\w$]*)[^{};]*\{"]
    elif LANGUAGE == "rust":
        patterns = [r"\bimpl(?:\s*<[^{}]*>)?\s+(?:[^{}]*?\s+for\s+)?([A-Za-z_][\w:]*)[^{};]*\{"]
    elif LANGUAGE == "swift":
        patterns = [r"\b(?:class|struct|enum|actor|extension)\s+([A-Za-z_]\w*)[^{}]*\{"]
    else:
        return []
    out: list[tuple[int, int, str]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, masked, re.MULTILINE):
            opening = masked.find("{", match.start(), match.end())
            closing = _matching_brace(masked, opening)
            if closing is not None:
                out.append((opening, closing, match.group(1)))
    return out


def _prefix_at(offset: int, type_ranges: Sequence[tuple[int, int, str]], masked: str) -> str:
    candidates = [value for value in type_ranges if value[0] < offset < value[1]]
    if candidates:
        return min(candidates, key=lambda value: value[1] - value[0])[2]
    if LANGUAGE == "objective-c":
        start = masked.rfind("@implementation", 0, offset)
        end = masked.rfind("@end", 0, offset)
        if start > end:
            match = re.match(r"@implementation\s+([A-Za-z_]\w*)", masked[start:])
            if match:
                return match.group(1)
    return ""


def _selector_name(signature: str) -> str:
    parts = re.findall(r"([A-Za-z_]\w*)\s*:", signature)
    if parts:
        return ":".join(parts) + ":"
    match = re.search(r"([A-Za-z_]\w*)", signature)
    return match.group(1) if match else "method"


def _function_matches(masked: str) -> Iterable[tuple[str, int, int]]:
    type_ranges = _type_ranges(masked)
    patterns: list[tuple[str, str]] = []
    if LANGUAGE == "typescript":
        patterns = [
            ("plain", r"\bfunction\s+([A-Za-z_$][\w$]*)\s*(?:<[^{};]*>)?\s*\([^{};]*\)\s*(?::[^={};]+)?\s*\{"),
            ("arrow", r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^{};]*\)|[A-Za-z_$][\w$]*)\s*(?::[^=;{}]+)?=>\s*\{"),
            ("method", r"(?:^|[;}\n])\s*(?:(?:public|private|protected|static|async|readonly|abstract|override|get|set)\s+)*([A-Za-z_$][\w$]*)\s*(?:<[^{};]*>)?\s*\([^{};]*\)\s*(?::[^={};]+)?\s*\{"),
        ]
    elif LANGUAGE == "rust":
        patterns = [("method", r"\bfn\s+([A-Za-z_]\w*)\s*(?:<[^{};]*>)?\s*\([^{};]*\)[^{};]*\{")]
    elif LANGUAGE == "swift":
        patterns = [
            ("method", r"\bfunc\s+([A-Za-z_]\w*)\s*(?:<[^{};]*>)?\s*\([^{};]*\)[^{};]*\{"),
            ("method", r"\b(init|deinit|subscript)\s*(?:\([^{};]*\))?[^{};]*\{"),
        ]
    elif LANGUAGE == "objective-c":
        patterns = [
            ("objc", r"[-+]\s*\([^)]*\)\s*([^;{}]+?)\s*\{"),
            ("c", r"(?:^|\n)\s*(?:[A-Za-z_]\w*\s+|[*]\s*)+([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{"),
        ]
    else:
        patterns = [
            ("bash", r"(?:^|\n)\s*(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(\s*\))?\s*\{"),
        ]

    excluded = {"if", "for", "while", "switch", "catch", "match", "guard", "else", "do"}
    seen: set[tuple[int, int]] = set()
    for kind, pattern in patterns:
        for match in re.finditer(pattern, masked, re.MULTILINE):
            raw_name = match.group(1).strip()
            name = _selector_name(raw_name) if kind == "objc" else raw_name
            if name in excluded:
                continue
            opening = masked.find("{", match.start(), match.end())
            if opening < 0:
                continue
            closing = _matching_brace(masked, opening)
            if closing is None or (opening, closing) in seen:
                continue
            seen.add((opening, closing))
            prefix = _prefix_at(opening, type_ranges, masked)
            if prefix:
                separator = "::" if LANGUAGE == "rust" else "."
                name = f"{prefix}{separator}{name}"
            yield name, opening, closing


def _complexity(body: str) -> int:
    patterns = {
        "typescript": [r"\bif\b", r"\bfor\b", r"\bwhile\b", r"\bcatch\b", r"\bcase\b", r"&&", r"\|\|", r"\?\?"],
        "rust": [r"\bif\b", r"\bfor\b", r"\bwhile\b", r"=>", r"&&", r"\|\|"],
        "swift": [r"\bif\b", r"\bguard\b", r"\bfor\b", r"\bwhile\b", r"\bcase\b", r"\bcatch\b", r"&&", r"\|\|"],
        "objective-c": [r"\bif\b", r"\bfor\b", r"\bwhile\b", r"\bcase\b", r"@catch\b", r"&&", r"\|\|", r"\?(?![?.])"],
        "bash": [r"\bif\b", r"\belif\b", r"\bfor\b", r"\bwhile\b", r"\buntil\b", r"\bcase\b", r"&&", r"\|\|"],
    }[LANGUAGE]
    return 1 + sum(len(re.findall(pattern, body)) for pattern in patterns)


def extract_functions(path: Path) -> list[FunctionMetric]:
    text = path.read_text(encoding="utf-8", errors="replace")
    masked = mask_non_code(text)
    out: list[FunctionMetric] = []
    for name, opening, closing in _function_matches(masked):
        body = masked[opening + 1 : closing]
        out.append(
            FunctionMetric(
                name=name,
                file=path.as_posix(),
                start_line=_line_at(text, opening),
                end_line=_line_at(text, closing),
                complexity=_complexity(body),
                coverage=None,
                crap=None,
            )
        )
    if LANGUAGE == "bash" and not out and masked.strip():
        out.append(FunctionMetric("<script>", path.as_posix(), 1, text.count("\n") + 1, _complexity(masked), None, None))
    return out


def _simple_json(payload: dict[str, object]) -> dict[str, list[Segment]]:
    out: dict[str, list[Segment]] = {}
    files = payload.get("files", {})
    if not isinstance(files, dict):
        return out
    for filename, raw in files.items():
        if not isinstance(raw, dict) or "executed_lines" not in raw:
            continue
        executed = {int(value) for value in raw.get("executed_lines", [])}
        missing = {int(value) for value in raw.get("missing_lines", [])}
        out[normalize_path(str(filename))] = [Segment(line, line, 1 if line in executed else 0) for line in sorted(executed | missing)]
    return out


def _istanbul_json(payload: dict[str, object]) -> dict[str, list[Segment]]:
    out: dict[str, list[Segment]] = {}
    for filename, raw in payload.items():
        if not isinstance(raw, dict) or "statementMap" not in raw or "s" not in raw:
            continue
        statement_map = raw.get("statementMap", {})
        counts = raw.get("s", {})
        segments: list[Segment] = []
        if isinstance(statement_map, dict) and isinstance(counts, dict):
            for key, location in statement_map.items():
                if not isinstance(location, dict):
                    continue
                start = location.get("start", {})
                end = location.get("end", {})
                if isinstance(start, dict) and isinstance(end, dict):
                    segments.append(Segment(int(start.get("line", 0)), int(end.get("line", start.get("line", 0))), int(counts.get(key, 0))))
        out[normalize_path(str(filename))] = segments
    return out


def _llvm_json(payload: dict[str, object]) -> dict[str, list[Segment]]:
    out: dict[str, list[Segment]] = {}
    data = payload.get("data", [])
    if not isinstance(data, list):
        return out
    for item in data:
        if not isinstance(item, dict):
            continue
        functions = item.get("functions", [])
        if not isinstance(functions, list):
            continue
        for function in functions:
            if not isinstance(function, dict):
                continue
            filenames = function.get("filenames", [])
            regions = function.get("regions", [])
            if not isinstance(filenames, list) or not isinstance(regions, list):
                continue
            for region in regions:
                if not isinstance(region, list) or len(region) < 6:
                    continue
                file_index = int(region[5])
                if file_index < 0 or file_index >= len(filenames):
                    continue
                filename = normalize_path(str(filenames[file_index]))
                out.setdefault(filename, []).append(Segment(int(region[0]), int(region[2]), int(region[4])))
    return out


def _cobertura(path: Path) -> dict[str, list[Segment]]:
    out: dict[str, list[Segment]] = {}
    root = ET.parse(path).getroot()
    for class_node in root.findall(".//class"):
        filename = class_node.attrib.get("filename")
        if not filename:
            continue
        segments: list[Segment] = []
        for line in class_node.findall("./lines/line"):
            number = int(line.attrib.get("number", "0"))
            hits = int(float(line.attrib.get("hits", "0")))
            segments.append(Segment(number, number, hits))
        out[normalize_path(filename)] = segments
    return out


def load_coverage(path: Path) -> dict[str, list[Segment]]:
    if path.suffix.lower() == ".xml":
        return _cobertura(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("coverage report must contain a JSON object")
    simple = _simple_json(payload)
    if simple:
        return simple
    llvm = _llvm_json(payload)
    if llvm:
        return llvm
    return _istanbul_json(payload)


def _segments_for_file(coverage: dict[str, list[Segment]], filename: str) -> list[Segment]:
    normalized = normalize_path(filename)
    if normalized in coverage:
        return coverage[normalized]
    candidates = [segments for key, segments in coverage.items() if key.endswith("/" + normalized) or normalized.endswith("/" + key)]
    return candidates[0] if len(candidates) == 1 else []


def score(complexity: int, coverage_percent: float) -> float:
    uncovered = 1.0 - coverage_percent / 100.0
    return complexity * complexity * uncovered**3 + complexity


def apply_coverage(metrics: Iterable[FunctionMetric], coverage: dict[str, list[Segment]]) -> list[FunctionMetric]:
    out: list[FunctionMetric] = []
    for metric in metrics:
        segments = [segment for segment in _segments_for_file(coverage, metric.file) if not (segment.end_line < metric.start_line or segment.start_line > metric.end_line)]
        if not segments:
            out.append(metric)
            continue
        covered = sum(segment.count > 0 for segment in segments)
        percent = 100.0 * covered / len(segments)
        out.append(FunctionMetric(**{**metric.to_dict(), "coverage": percent, "crap": score(metric.complexity, percent)}))
    return out


def analyze(root: Path, coverage_path: Path | None, filters: Sequence[str] = ()) -> list[FunctionMetric]:
    metrics: list[FunctionMetric] = []
    for path in discover_files(root, filters):
        metrics.extend(extract_functions(path))
    if coverage_path is not None and coverage_path.exists():
        metrics = apply_coverage(metrics, load_coverage(coverage_path))
    return sorted(metrics, key=lambda metric: (metric.crap is None, -(metric.crap or -math.inf), metric.file, metric.name))


def run_test_command(command: str, root: Path) -> None:
    completed = subprocess.run(command, cwd=root, shell=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"test command failed with exit code {completed.returncode}: {command}")


def format_report(metrics: Sequence[FunctionMetric]) -> str:
    header = f"{'Function':38} {'File':44} {'CC':>4} {'Cov%':>7} {'CRAP':>8}"
    lines = ["CRAP Report", "===========", header, "-" * len(header)]
    for metric in metrics:
        coverage = "N/A" if metric.coverage is None else f"{metric.coverage:.1f}%"
        crap = "N/A" if metric.crap is None else f"{metric.crap:.1f}"
        lines.append(f"{metric.name[:38]:38} {metric.file[:44]:44} {metric.complexity:4d} {coverage:>7} {crap:>8}")
    return "\n".join(lines) + "\n"
