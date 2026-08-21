import json
from pathlib import Path

from crap4swift.core import analyze, extract_functions, score


def test_score() -> None:
    assert score(10, 50.0) == 22.5
    assert score(10, 100.0) == 10.0


def test_extracts_function_and_complexity(tmp_path: Path) -> None:
    source = tmp_path / 'sample.swift'
    source.write_text('func choose(_ value: Int) -> Int {\n    if value > 0 && value < 10 { return 1 }\n    return 0\n}\n', encoding="utf-8")
    metric = extract_functions(source)[0]
    assert metric.name.endswith('choose')
    assert metric.complexity == 3


def test_maps_simple_line_coverage(tmp_path: Path) -> None:
    source = tmp_path / 'sample.swift'
    source.write_text('func choose(_ value: Int) -> Int {\n    if value > 0 && value < 10 { return 1 }\n    return 0\n}\n', encoding="utf-8")
    coverage = tmp_path / "coverage.json"
    coverage.write_text(json.dumps({"files": {'sample.swift': {"executed_lines": [1, 2, 4], "missing_lines": [3]}}}), encoding="utf-8")
    metric = analyze(tmp_path, coverage)[0]
    assert metric.coverage is not None
    assert metric.crap is not None
