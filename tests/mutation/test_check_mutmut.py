"""Tests for the mutmut CI report checker."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest
from click import unstyle

from mutation import check_mutmut
from mutation.check_mutmut import MINIMUM_MUTATION_SCORE, analyze_mutmut_report_passed

EMPTY_REPORT = {
    "killed": 0,
    "survived": 0,
    "total": 0,
    "no_tests": 0,
    "skipped": 0,
    "suspicious": 0,
    "timeout": 0,
    "check_was_interrupted_by_user": 0,
    "segfault": 0,
}
REPORT_WITH_TIMEOUT = EMPTY_REPORT | {"killed": 3, "survived": 1, "total": 5, "timeout": 1}


def test_module_output_from_mutation_directory_is_exact(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The standalone module finds the repository report and exits without a traceback."""
    module_dir = tmp_path / "mutation"
    module_dir.mkdir()
    checker = Path(__file__).parents[2] / "mutation" / "check_mutmut.py"
    report = tmp_path / "mutants" / "mutmut-cicd-stats.json"
    report.parent.mkdir()
    report.write_text(
        json.dumps({"killed": 1, "survived": 1, "total": 2, "skipped": 0, "timeout": 0}), encoding="utf-8"
    )

    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(checker), run_name="__main__")

    assert exc_info.value.code == 1
    output = " ".join(unstyle(capsys.readouterr().out).split())
    leading_rule, title, results = output.partition("MUTMUT MUTATION RESULTS")
    assert title == "MUTMUT MUTATION RESULTS"
    assert not leading_rule.strip("─ ")
    assert results.strip("─ ") == ("killed 1 survived 1 total 2 skipped 0 timeout 0 Mutation Score: 50.0")

    monkeypatch.chdir(module_dir)
    monkeypatch.setattr(check_mutmut, "__file__", str(module_dir / "check_mutmut.py"))
    assert check_mutmut.update_mutation_score() == pytest.approx(50.0)
    badge = json.loads((tmp_path / "mutation-score.json").read_text(encoding="utf-8"))
    assert badge["message"] == "50.0%"


def test_report_with_timeout_passes_and_renders(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A timeout counts as detected and remains visible in the report."""
    report = tmp_path / "mutants" / "mutmut-cicd-stats.json"
    report.parent.mkdir()
    report.write_text(json.dumps(REPORT_WITH_TIMEOUT), encoding="utf-8")

    checker = Path(__file__).parents[2] / "mutation" / "check_mutmut.py"
    monkeypatch.chdir(tmp_path)
    runpy.run_path(str(checker), run_name="__main__")

    output = " ".join(unstyle(capsys.readouterr().out).split())
    assert "MUTMUT MUTATION RESULTS" in output
    assert "timeout 1" in output
    assert "Mutation Score: 80.0" in output
    assert json.loads((tmp_path / "mutation-score.json").read_text(encoding="utf-8")) == {
        "schemaVersion": 1,
        "label": "mutation",
        "message": "80.0%",
        "color": "#177445",
    }


def test_report_checker_entry_point_exits_zero_and_prints_score(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The script entry point reads the default report path and exposes pass/fail through the process exit."""
    report = tmp_path / "mutants" / "mutmut-cicd-stats.json"
    report.parent.mkdir()
    report.write_text(json.dumps(REPORT_WITH_TIMEOUT), encoding="utf-8")

    checker = Path(__file__).parents[2] / "mutation" / "check_mutmut.py"
    monkeypatch.chdir(tmp_path)
    runpy.run_path(str(checker), run_name="__main__")

    output = " ".join(unstyle(capsys.readouterr().out).split())
    assert "MUTMUT MUTATION RESULTS" in output
    assert "Mutation Score: 80.0" in output


def test_report_enforces_threshold(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation scores at or above the minimum pass, while lower scores fail."""
    data = REPORT_WITH_TIMEOUT.copy()
    report = tmp_path / "mutmut-cicd-stats.json"
    report.write_text(json.dumps(data), encoding="utf-8")
    mutation_score = analyze_mutmut_report_passed(str(report))
    assert MINIMUM_MUTATION_SCORE <= mutation_score < 100.0

    passing_report = {"killed": MINIMUM_MUTATION_SCORE, "timeout": 0, "total": 100, "skipped": 0}
    report.write_text(json.dumps(passing_report), encoding="utf-8")
    assert analyze_mutmut_report_passed(str(report)) == pytest.approx(MINIMUM_MUTATION_SCORE)

    failing_score = MINIMUM_MUTATION_SCORE - 1
    report.write_text(json.dumps({"killed": failing_score, "timeout": 0, "total": 100, "skipped": 0}), encoding="utf-8")
    assert analyze_mutmut_report_passed(str(report)) == pytest.approx(failing_score)
    output = " ".join(unstyle(capsys.readouterr().out).split())
    assert f"Mutation Score: {failing_score}" in output


def test_entry_point_fails_below_threshold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The script entry point exits 1 when the score is below the minimum."""
    report = tmp_path / "mutants" / "mutmut-cicd-stats.json"
    report.parent.mkdir()
    report.write_text(json.dumps(EMPTY_REPORT), encoding="utf-8")

    checker = Path(__file__).parents[2] / "mutation" / "check_mutmut.py"
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(checker), run_name="__main__")

    assert exc_info.value.code == 1


def test_missing_report_returns_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A missing export reports the error and returns a zero score."""
    assert analyze_mutmut_report_passed(str(tmp_path / "missing.json")) == pytest.approx(0.0)
    assert "Mutmut report not" in capsys.readouterr().out


def test_malformed_report_fails(tmp_path: Path) -> None:
    """Malformed JSON is reported as malformed JSON."""
    report = tmp_path / "mutmut-cicd-stats.json"
    report.write_text("{", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        analyze_mutmut_report_passed(str(report))
