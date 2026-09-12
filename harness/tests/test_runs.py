"""Tests for harness.runs (run-log read-back) and the `harness status` command built on it.

Real sample logs captured from actual runs live in harness/tests/logs/ (claude 0003, codex 0003) and are
only ever read. Synthetic logs cover shapes the samples do not (corrupt lines, missing facts, unknown
agents) and are written to each test's own tmp_path, so tests never collide under xdist. No test runs an
agent or spends a cent.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import unittest.mock
from pathlib import Path

import pytest
from click import unstyle
from hypothesis import given
from hypothesis import strategies as st
from rich.console import Console

from harness import cli, runs
from harness.runs import BLANK
from harness.tests.test_cli import runner

LOGS = Path(__file__).parent / "logs"

CLAUDE_FACTS = {
    "when": "2026-09-02T06:34",
    "iterations": "1",
    "minutes": "0",
    "tokens_in": "2",
    "tokens_out": "13",
    "cache_read": "5903",
    "cache_write": "13757",
    "cost_usd": "$0.14",
    "ms": "1800",
    "stopped": "end_turn",
    "message": "I don't have access to my session ID.",
}
CODEX_FACTS = {
    "when": "2026-09-02T05:38",
    "iterations": "1",
    "minutes": "1",
    "tokens_in": "15376",
    "tokens_out": "142",
    "cache_read": "9600",
    "cache_write": "0",
    "cost_usd": BLANK,
    "ms": BLANK,
    "stopped": BLANK,
    "message": "Hi — `codex-0003` here. No edits made. No big issues observed from the provided context.",
}


@pytest.mark.parametrize(
    ("log", "facts"),
    [
        (LOGS / "claude" / "0003.jsonl", CLAUDE_FACTS),
        (LOGS / "codex" / "0003.jsonl", CODEX_FACTS),
    ],
    ids=("claude", "codex"),
)
def test_real_sample_logs_map_to_the_same_facts(log: Path, facts: dict[str, str]) -> None:
    """Claude and Codex report the same facts under different names; both map onto one row shape."""
    row = runs.read_runs([log])[0]

    assert {name: getattr(row, name) for name in facts} == facts
    assert row.agent == log.parent.name
    assert row.run == log.stem


def test_claude_sample_ignores_session_noise() -> None:
    """Only the result and ralph lines carry facts; init, hooks, assistant, and rate-limit lines do not."""
    row = runs.read_runs([LOGS / "claude" / "0003.jsonl"])[0]

    assert row.iterations == "1"  # from the ralph framing lines, not the assistant turn
    assert row.ms == "1800"  # result.duration_ms, not assistant ttft_ms or duration_api_ms


def test_row_keeps_the_log_path_it_was_given() -> None:
    """A row's log path is exactly the path read, so verbose output points at the real file."""
    log = LOGS / "codex" / "0003.jsonl"

    assert runs.read_runs([log])[0].log == str(log)


def test_partial_run_without_result_line_falls_back_to_ralph_facts(tmp_path: Path) -> None:
    """A log cut mid-run still reports iterations and its start timestamp from the framing lines."""
    log = tmp_path / "0001.jsonl"
    log.write_text(
        '{"type":"ralph","iteration":2,"max_iterations":3,"timestamp":"2026-09-02T05:38"}\n', encoding="utf-8"
    )

    row = runs.read_runs([log])[0]

    assert row.iterations == "2"  # started iteration while no completed line exists
    assert row.when == "2026-09-02T05:38"
    assert row.tokens_in == BLANK
    assert row.stopped == BLANK
    assert row.message == BLANK


def test_missing_logs_are_skipped_entirely() -> None:
    """A log that cannot be read contributes no row rather than crashing or showing zeros."""
    assert runs.read_runs([LOGS / "codex" / "missing.jsonl"]) == []


def test_corrupt_lines_are_skipped_not_fatal(tmp_path: Path) -> None:
    """Garbage and non-object lines are passed over; the readable rest of the log still yields facts."""
    log = tmp_path / "0002.jsonl"
    log.write_text(
        'not json at all\n[1, 2, 3]\n{"type":"turn.completed","usage":{"input_tokens":9,"output_tokens":4}}\n',
        encoding="utf-8",
    )

    row = runs.read_runs([log])[0]

    assert row.tokens_in == "9"
    assert row.tokens_out == "4"


def test_codex_cache_field_names_map_to_claude_names(tmp_path: Path) -> None:
    """Codex's cached/cache_write token names land in the same columns Claude's read/write do."""
    log = tmp_path / "0002.jsonl"
    log.write_text(
        '{"type":"turn.completed","usage":{"cached_input_tokens":7,"cache_write_input_tokens":3}}\n', encoding="utf-8"
    )

    row = runs.read_runs([log])[0]

    assert row.cache_read == "7"
    assert row.cache_write == "3"


def test_unreported_facts_render_as_dashes_never_zeros(tmp_path: Path) -> None:
    """An agent that logs nothing at all still gets a row of dashes, not a table of zeros."""
    log = tmp_path / "0001.jsonl"
    log.write_text("aider printed this\n", encoding="utf-8")

    row = runs.read_runs([log])[0]

    assert row.when == BLANK  # nothing is invented for a log with no ralph framing lines
    assert row.iterations == row.minutes == BLANK
    assert row.tokens_in == row.tokens_out == row.cache_read == row.cache_write == BLANK
    assert row.cost_usd == row.ms == row.stopped == row.message == BLANK


def test_elapsed_minutes_need_readable_endpoints(tmp_path: Path) -> None:
    """A span is computed only from two readable first-and-last timestamps in order, else it is a dash."""
    log = tmp_path / "0002.jsonl"
    log.write_text(
        '{"type":"ralph","iteration":1,"timestamp":"2026-09-02T05:38"}\n'
        '{"type":"ralph","iteration":2,"timestamp":"garbage"}\n'
        '{"type":"ralph","completed":2,"timestamp":"2026-09-02T05:41"}\n',
        encoding="utf-8",
    )
    row = runs.read_runs([log])[0]

    assert row.minutes == "3"  # a garbage middle line never breaks the span
    assert row.iterations == "2"

    log.write_text(
        '{"type":"ralph","iteration":1,"timestamp":"garbage"}\n'
        '{"type":"ralph","completed":2,"timestamp":"2026-09-02T05:41"}\n',
        encoding="utf-8",
    )
    row = runs.read_runs([log])[0]

    assert row.minutes == BLANK
    assert row.iterations == "2"


@given(junk=st.text(min_size=1))
def test_bad_cost_and_timestamp_values_render_as_dashes(junk: str) -> None:
    """Missing or unreadable totals and timestamps are reported as absent, whatever the agent wrote."""
    with tempfile.TemporaryDirectory() as scratch:
        log = Path(scratch) / "0002.jsonl"
        log.write_text(
            json.dumps({"type": "ralph", "iteration": 1})
            + "\n"
            + json.dumps({"type": "result", "total_cost_usd": junk, "duration_ms": junk})
            + "\n",
            encoding="utf-8",
        )
        row = runs.read_runs([log])[0]

    assert row.minutes == BLANK
    assert row.when == BLANK  # no framing timestamp anywhere: nothing is invented
    for shown in (row.cost_usd, row.ms):
        assert shown == BLANK or math.isfinite(float(shown.lstrip("$")))


NUMBERS = st.integers(min_value=0, max_value=10**9) | st.floats(min_value=0, max_value=1e9, allow_nan=False)


@given(cost=NUMBERS | st.none(), duration=NUMBERS | st.none())
def test_cost_and_duration_accept_any_number_the_next_agent_might_log(
    cost: float | None, duration: float | None
) -> None:
    """Whatever numeric shape a future agent emits, rendering stays a string and never raises."""
    with tempfile.TemporaryDirectory() as scratch:
        log = Path(scratch) / "0002.jsonl"
        log.write_text(
            json.dumps({"type": "result", "total_cost_usd": cost, "duration_ms": duration}) + "\n", encoding="utf-8"
        )
        row = runs.read_runs([log])[0]

    assert row.cost_usd == (BLANK if cost is None else f"${cost:.2f}")
    assert row.ms == (BLANK if duration is None else f"{duration:.0f}")


@given(text=st.text(min_size=1))
def test_message_text_is_always_a_single_line_or_a_dash(text: str) -> None:
    """Any text an agent might print collapses to one line and never crashes the reader."""
    with tempfile.TemporaryDirectory() as scratch:
        log = Path(scratch) / "0002.jsonl"
        log.write_text(json.dumps({"type": "result", "result": text}) + "\n", encoding="utf-8")
        row = runs.read_runs([log])[0]

    assert "\n" not in row.message
    assert "\r" not in row.message
    assert row.message == BLANK or row.message


def test_iter_logs_is_ordered_when_the_runs_directory_exists(git_repo: Path) -> None:
    """Logs come back oldest-first across dated and agent directories; missing roots yield nothing."""
    runs_dir = git_repo / "scratchpad" / "runs"
    assert runs.iter_logs(runs_dir / "absent") == []

    for path in ("20260901/codex/0001.jsonl", "20260902/claude/0001.jsonl", "20260902/codex/0001.jsonl"):
        (runs_dir / path).parent.mkdir(parents=True, exist_ok=True)
        (runs_dir / path).touch()

    assert [log.parent.name for log in runs.iter_logs(runs_dir)] == ["codex", "claude", "codex"]


def test_status_table_summarizes_real_sample_logs(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The default command renders one row per run with cost, tokens, and stop reason; newest first."""
    monkeypatch.setattr(cli, "REPO_ROOT", git_repo)
    runs_dir = git_repo / "scratchpad" / "runs" / "20260902"
    for source in ("claude/0003.jsonl", "codex/0003.jsonl"):
        destination = runs_dir / source
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((LOGS / source).read_bytes())
    os.utime(runs_dir / "claude" / "0003.jsonl", (1, 1))  # written before the codex run: older

    with unittest.mock.patch.object(cli, "console", Console(width=512, color_system=None, force_terminal=False)):
        result = runner.invoke(cli.app, ["status"])

    assert result.exit_code == 0
    flat = " ".join(unstyle(result.output).split())
    assert "2 run(s)" in flat
    assert "2026-09-02T06:34 claude 0003 1 0 2 13 5903 13757 $0.14 1800 end_turn" in flat
    assert "2026-09-02T05:38 codex 0003 1 1 15376 142 9600 0 - - -" in flat
    assert flat.index("codex 0003") < flat.index("claude 0003")  # newest run first


def test_status_verbose_adds_last_message_and_log_path(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--verbose appends each run's agent message and its log path, so a reader can open the right file."""
    monkeypatch.setattr(cli, "REPO_ROOT", git_repo)
    destination = git_repo / "scratchpad" / "runs" / "20260902" / "codex" / "0003.jsonl"
    destination.parent.mkdir(parents=True)
    destination.write_bytes((LOGS / "codex" / "0003.jsonl").read_bytes())

    with unittest.mock.patch.object(cli, "console", Console(width=512, color_system=None, force_terminal=False)):
        result = runner.invoke(cli.app, ["status", "--verbose"])

    assert result.exit_code == 0
    flat = " ".join(unstyle(result.output).split())
    assert "No big issues observed from the provided context." in flat
    assert str(destination) in flat


def test_status_with_no_logs_matches_the_old_receipt_count(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty runs directory keeps the one-line receipt the old status printed."""
    monkeypatch.setattr(cli, "REPO_ROOT", git_repo)

    result = runner.invoke(cli.app, ["status"])

    assert result.exit_code == 0
    assert result.stdout == f"0 run log(s) in {git_repo / 'scratchpad' / 'runs'}\n"
