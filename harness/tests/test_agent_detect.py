"""Tests for harness/agent_detect.sh, which decides whether containment stays on for a commit.

The script is fail-closed: it exits 0 (treat as agent) unless it can see a human at an interactive
terminal. These tests pin that direction, because the failure that matters is the silent one --
an agent walking through with containment off leaves no trace, while a human caught by mistake
gets a message and can use --no-verify.

Every case supplies a stub `ps` on PATH. The suite is normally started by an agent, so the real `ps`
reports that agent in the script's own ancestry and signal 2 fires before the case under test can.
Detaching the child does not help: orphans here re-parent to a subreaper that leads back to the same
agent. `ps` is the seam the script already uses, so replacing it is the honest way in.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "harness" / "agent_detect.sh"

# No agent marker set. PATH is filled in per-case so the stub `ps` is found before the real one.
BARE_ENV = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}

# `ps -o ppid=,comm= -p N` answers with a parent of 1, ending the walk after one harmless step.
# `ps -o tty= -p N` answers "?", the no-controlling-terminal marker. Together: no agent, no human.
NO_AGENT_NO_TERMINAL = """#!/bin/sh
case "$*" in
    *tty*) echo "?" ;;
    *) echo "1 sh" ;;
esac
"""

# No agent, but a controlling terminal: a human typing at a shell.
NO_AGENT_AT_TERMINAL = """#!/bin/sh
case "$*" in
    *tty*) echo "pts/3" ;;
    *) echo "1 sh" ;;
esac
"""

# An agent binary in the ancestry, at a terminal. This is the CLI-agent case.
AGENT_AT_TERMINAL = """#!/bin/sh
case "$*" in
    *tty*) echo "pts/3" ;;
    *) echo "1 codex" ;;
esac
"""


def run(
    tmp_path: Path, ps_stub: str, *arguments: str, **markers: str
) -> subprocess.CompletedProcess[str]:
    """Run the script against a stub `ps` that decides what ancestry and terminal it sees."""
    stub_directory = tmp_path / "bin"
    stub_directory.mkdir(exist_ok=True)
    stub = stub_directory / "ps"
    stub.write_text(ps_stub)
    stub.chmod(0o755)

    environment = {**BARE_ENV, **markers, "PATH": f"{stub_directory}:{BARE_ENV['PATH']}"}
    # start_new_session detaches the child from any controlling terminal, so /dev/tty is never
    # openable and the stub's `ps -o tty=` answer is the only thing deciding signal 3. Without it a
    # developer running pytest from a terminal would get different results than CI does.
    return subprocess.run(
        [str(SCRIPT), *arguments],
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        start_new_session=True,
    )


def test_no_terminal_is_treated_as_an_agent(tmp_path: Path) -> None:
    """The fail-closed rule, and the only one that catches an IDE agent.

    An editor extension spawns git through child_process with no pty, leaves no agent name in the
    process tree, and exports no marker variable. Nothing else in the script would see it.
    """
    completed = run(tmp_path, NO_AGENT_NO_TERMINAL)
    assert completed.returncode == 0
    assert "no controlling terminal" in completed.stdout


def test_interactive_terminal_without_markers_is_a_human(tmp_path: Path) -> None:
    """A human typing `git commit` has a pty on stdin and no agent fingerprint: containment off."""
    completed = run(tmp_path, NO_AGENT_AT_TERMINAL)
    assert completed.returncode == 1
    assert not completed.stdout


def test_agent_in_the_ancestry_beats_an_interactive_terminal(tmp_path: Path) -> None:
    """A CLI agent that unset its own variables is still in the process tree it runs in."""
    completed = run(tmp_path, AGENT_AT_TERMINAL)
    assert completed.returncode == 0
    assert "process ancestor codex" in completed.stdout


def test_marker_variable_beats_an_interactive_terminal(tmp_path: Path) -> None:
    """Some agents allocate a pty for their shell tool, so signal 3 alone would not be enough."""
    completed = run(tmp_path, NO_AGENT_AT_TERMINAL, CLAUDECODE="1")
    assert completed.returncode == 0
    assert "environment marker CLAUDECODE" in completed.stdout


def test_an_empty_marker_variable_does_not_count(tmp_path: Path) -> None:
    """`RALPH_LOOP=` exported empty is not a claim that an agent is running."""
    completed = run(tmp_path, NO_AGENT_AT_TERMINAL, RALPH_LOOP="")
    assert completed.returncode == 1


def test_explain_reports_every_signal_and_a_verdict(tmp_path: Path) -> None:
    """--explain is how someone confirms behaviour in an editor this repo cannot test from here."""
    completed = run(tmp_path, NO_AGENT_NO_TERMINAL, "--explain")
    assert completed.returncode == 0
    for label in ("environment marker", "agent in ancestry", "human at a terminal", "verdict"):
        assert label in completed.stdout
    assert "AGENT" in completed.stdout


def test_explain_says_human_when_a_terminal_is_attached(tmp_path: Path) -> None:
    """The same report, on the other side of the decision."""
    completed = run(tmp_path, NO_AGENT_AT_TERMINAL, "--explain")
    assert completed.returncode == 1
    assert "verdict             : human" in completed.stdout
