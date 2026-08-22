import json
from pathlib import Path

import pytest

from crap4swift.core import AnalysisError, analyze, extract_functions, score


def test_score_formula() -> None:
    assert score(10, 50.0) == 22.5
    assert score(10, 100.0) == 10.0


def test_target_language_function_and_complexity(tmp_path: Path) -> None:
    source = tmp_path / 'sample.swift'
    source.write_text('struct Choice {\n  func choose(_ a: Bool, _ b: Bool) -> Int {\n    if a && b { return 1 }\n    return 0\n  }\n}\n', encoding="utf-8")
    metrics = extract_functions(source, tmp_path)
    assert metrics
    metric = next(item for item in metrics if 'choose' in item.name)
    assert metric.complexity >= 3


def test_lcov_is_mapped_by_executable_line(tmp_path: Path) -> None:
    source = tmp_path / 'sample.swift'
    source.write_text('struct Choice {\n  func choose(_ a: Bool, _ b: Bool) -> Int {\n    if a && b { return 1 }\n    return 0\n  }\n}\n', encoding="utf-8")
    coverage = tmp_path / "lcov.info"
    coverage.write_text(f"SF:{source.as_posix()}\nDA:1,1\nDA:2,1\nDA:3,0\nDA:4,1\nDA:5,1\nend_of_record\n", encoding="utf-8")
    metrics = analyze(tmp_path, coverage)
    assert metrics
    assert any(item.coverage is not None for item in metrics)


def test_missing_report_fails(tmp_path: Path) -> None:
    with pytest.raises(AnalysisError):
        analyze(tmp_path, tmp_path / "missing.info")
