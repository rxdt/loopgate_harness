"""Tests for the mutmut CI report checker."""

from __future__ import annotations

import contextlib
import json
import runpy
from pathlib import Path

import pytest
import typer
from click import unstyle

from mutation.check_mutmut import analyze_mutmut_report


def test_report_with_timeout_passes_and_renders(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A timeout counts as detected and remains visible in the report."""
    report = tmp_path / "mutants" / "mutmut-cicd-stats.json"
    report.parent.mkdir()
    report.write_text(
        Path(__file__).with_name("mutmut-cicd-stats.json").read_text(encoding="utf-8"), encoding="utf-8"
    )

    checker = Path(__file__).parents[2] / "mutation" / "check_mutmut.py"
    with contextlib.chdir(tmp_path):
        runpy.run_path(str(checker), run_name="__main__")

    output = " ".join(unstyle(capsys.readouterr().out).split())
    assert "MUTMUT MUTATION RESULTS" in output
    assert "timeout 1" in output
    assert "MUTATION SCORE: 100.0" in output


def test_actionable_result_fails(tmp_path: Path) -> None:
    """A survived mutant is included in the report analysis."""
    data = json.loads(Path(__file__).with_name("mutmut-cicd-stats.json").read_text(encoding="utf-8"))
    data["survived"] = 1
    data["total"] += 1
    report = tmp_path / "mutmut-cicd-stats.json"
    report.write_text(json.dumps(data), encoding="utf-8")
    mutation_score = analyze_mutmut_report(report)
    assert mutation_score >= 60.0


def test_missing_report_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A missing export errors CI."""
    with pytest.raises(typer.Exit) as exc_info:
        analyze_mutmut_report(tmp_path / "missing.json")

    assert exc_info.value.exit_code == 1
    assert "Error: Mutmut JSON report not found" in capsys.readouterr().out


def test_malformed_report_fails(tmp_path: Path) -> None:
    """Malformed JSON is reported as malformed JSON."""
    report = tmp_path / "mutmut-cicd-stats.json"
    report.write_text("{", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        analyze_mutmut_report(report)
