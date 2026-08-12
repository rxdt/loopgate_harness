"""Tests for the Ralph loop script.

Ralph is deliberately dumb: it reads PROMPT.md, runs the worker under a timeout, prints a line, and
loops. It does not install hooks, verify the gate, or record runs. `set -eu` simply propagates the
worker's exit code and stops the loop on any failure. The install/gate-active behavior that used to
live here now lives in the `ralph` CLI and is tested in test_cli.py / test_integration.py.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from harness.tests.conftest import REPO_ROOT

RALPH = REPO_ROOT / "harness" / "ralph.sh"


def write_executable(path: Path, text: str) -> None:
    """Write an executable test helper script."""
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def write_passthrough_timeout(tmp_path: Path) -> Path:
    """Create a hermetic timeout stand-in that runs the command after its duration argument.

    Args:
        tmp_path: Directory where the executable is created.

    Returns:
        The executable timeout path.
    """
    timeout = tmp_path / "timeout"
    write_executable(timeout, '#!/bin/sh\nshift\nexec "$@"\n')
    return timeout


def run_ralph(
    tmp_path: Path,
    worker: Path,
    ralph_args: list[str] | None = None,
    timeout_executable: str | Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run Ralph with the selected timeout executable, defaulting to a hermetic test stand-in."""
    timeout = Path(timeout_executable) if timeout_executable is not None else None
    timeout = timeout or write_passthrough_timeout(tmp_path)
    env = os.environ.copy()
    env["TIMEOUT"] = str(timeout)
    env["RALPH_PROMPT"] = "do the most important thing"  # harness passes the prompt string (hermetic)
    command = [str(RALPH), *(ralph_args or []), str(worker)]
    return subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False, env=env)


def test_loop_passes_prompt_and_completes(tmp_path: Path) -> None:
    """Ralph uses the configured timeout, feeds the prompt to the worker, and completes."""
    timeout = tmp_path / "gtimeout"
    write_executable(
        timeout,
        "#!/bin/sh\nprintf 'gtimeout\\n' > used-timeout\nshift\nexec \"$@\"\n",
    )
    worker = tmp_path / "worker.sh"
    write_executable(worker, "#!/bin/sh\ncat > received-prompt.txt\nexit 0\n")
    result = run_ralph(tmp_path, worker, ["1", "1"], timeout_executable=timeout)
    assert result.returncode == 0
    assert (tmp_path / "used-timeout").read_text(encoding="utf-8") == "gtimeout\n"
    assert (tmp_path / "received-prompt.txt").read_text(encoding="utf-8") == (
        "do the most important thing\n\nRALPH_ITERATION=1/1\n"
    )
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert (events[0]["iteration"], events[-1]["completed"]) == (1, 1)


def test_nonzero_worker_exit_propagates_and_stops(tmp_path: Path) -> None:
    """A worker abort propagates its exit code (set -e) and stops Ralph before the next iteration."""
    worker = tmp_path / "worker.sh"
    write_executable(worker, "#!/bin/sh\nexit 7\n")
    result = run_ralph(tmp_path, worker, ["2", "1"])
    assert result.returncode == 7
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert [event["iteration"] for event in events] == [1]
    assert all("completed" not in event for event in events)


def test_timeout_propagates_and_stops(tmp_path: Path) -> None:
    """A timeout (exit 124) propagates and Ralph stops immediately."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable(bin_dir / "gtimeout", "#!/bin/sh\nexit 124\n")
    write_executable(bin_dir / "timeout", "#!/bin/sh\nexit 124\n")
    worker = tmp_path / "worker.sh"
    write_executable(worker, "#!/bin/sh\nexit 0\n")
    (tmp_path / "PROMPT.md").write_text("x\n", encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["TIMEOUT"] = str(bin_dir / "gtimeout")
    env["RALPH_PROMPT"] = "x"
    result = subprocess.run(
        [str(RALPH), "2", "1", str(worker)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 124
    assert "iteration 2/2" not in result.stderr
    assert "completed" not in result.stderr


def test_worker_args_pass_through_without_substitution(tmp_path: Path) -> None:
    """Ralph does no token substitution: worker args (e.g. a literal {{PROMPT}}) reach the worker
    byte-for-byte. The prompt is delivered only on stdin, never injected into argv.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    worker = tmp_path / "worker.sh"
    # Record argv on one channel and stdin on another, so we can prove where the prompt went.
    write_executable(worker, '#!/bin/sh\nprintf "%s\\n" "$@" > args.txt\ncat > stdin.txt\n')
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["TIMEOUT"] = str(write_passthrough_timeout(tmp_path))
    env["RALPH_PROMPT"] = "do the most important thing"
    result = subprocess.run(
        [str(RALPH), "1", "1", str(worker), "-i", "{{PROMPT}}"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0
    assert (tmp_path / "args.txt").read_text(encoding="utf-8") == "-i\n{{PROMPT}}\n"  # untouched argv
    assert (tmp_path / "stdin.txt").read_text(encoding="utf-8") == (
        "do the most important thing\n\nRALPH_ITERATION=1/1\n"  # prompt arrives only on stdin
    )


def test_script_has_no_bashisms() -> None:
    """The shell script parses as POSIX sh."""
    assert shutil.which("sh") is not None
    result = subprocess.run(["sh", "-n", str(RALPH)], capture_output=True, text=True, check=False)
    assert result.returncode == 0


def test_ralph_loop_env_reaches_worker(tmp_path: Path) -> None:
    """RALPH_LOOP=1 is exported into the worker's environment as the containment marker."""
    worker = tmp_path / "worker.sh"
    write_executable(worker, '#!/bin/sh\nprintf "%s" "$RALPH_LOOP" > loop.txt\n')
    result = run_ralph(tmp_path, worker, ["1", "1"])
    assert result.returncode == 0
    assert (tmp_path / "loop.txt").read_text(encoding="utf-8") == "1"


def test_worker_keeps_its_own_args(tmp_path: Path) -> None:
    """The agent command keeps its own flags and spaced args (\"$@\" is not re-split)."""
    (tmp_path / "PROMPT.md").write_text("p\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    worker = tmp_path / "worker.sh"
    write_executable(worker, '#!/bin/sh\nprintf "%s\\n" "$@" > args.txt\n')
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["TIMEOUT"] = str(write_passthrough_timeout(tmp_path))
    env["RALPH_PROMPT"] = "p"
    subprocess.run(
        [str(RALPH), "1", "1", str(worker), "--flag", "a b"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert (tmp_path / "args.txt").read_text(encoding="utf-8") == "--flag\na b\n"
