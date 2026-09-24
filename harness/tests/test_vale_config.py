"""Regression tests for the repository's Vale prose configuration."""

from __future__ import annotations

import subprocess
from pathlib import Path

from harness.tests.conftest import REPO_ROOT


def test_vale_checks_comments_and_docstrings_but_not_string_literals(tmp_path: Path) -> None:
    """The Python View should lint prose nodes without treating normal strings as prose."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        '"""We utilize a deliberately wordy verb in the module docstring."""\n'
        '\n'
        'class Example:\n'
        '    """We utilize another wordy verb in the class docstring."""\n'
        '\n'
        '    def method(self) -> str:\n'
        '        """We utilize the word again in the function docstring."""\n'
        '        return "utilize should stay ordinary code data"\n'
        '\n'
        '# In order to keep this comment clear, use the shorter phrase.\n',
        encoding="utf-8",
    )

    result = subprocess.run(
        ["vale", "--no-global", "--config", str(REPO_ROOT / ".vale.ini"), "--output=line", str(sample)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    output = result.stdout
    assert output.count("LoopGate.PlainWords") == 4
    assert "ordinary code data" not in output
    assert "sample.py:" in output


def test_vale_flags_overlong_docstring_sentence(tmp_path: Path) -> None:
    """The local sentence-length rule should report long Python docstring prose."""
    sample = tmp_path / "long_sentence.py"
    sentence = (
        "This deliberately long technical sentence keeps adding ordinary readable words so the local prose rule "
        "has enough real language to exceed its configured limit and report a clear warning to the contributor "
        "who wrote it today"
    )
    sample.write_text(f'"""{sentence}."""\n', encoding="utf-8")

    result = subprocess.run(
        ["vale", "--no-global", "--config", str(REPO_ROOT / ".vale.ini"), "--output=line", str(sample)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.count("LoopGate.SentenceLength") == 1
    assert "Sentence exceeds 30 words" in result.stdout
    assert "split it into shorter statements" in result.stdout

    short_sentences = tmp_path / "short_sentences.py"
    short_sentences.write_text(
        '"""This first sentence stays comfortably below the configured limit while still giving the docstring '
        'enough detail for a useful example. This second sentence also stays below the limit even though the '
        'whole paragraph contains more than thirty words in total."""\n',
        encoding="utf-8",
    )

    short_result = subprocess.run(
        [
            "vale",
            "--no-global",
            "--config",
            str(REPO_ROOT / ".vale.ini"),
            "--output=line",
            str(short_sentences),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert short_result.returncode == 0, short_result.stderr
    assert "LoopGate.SentenceLength" not in short_result.stdout
