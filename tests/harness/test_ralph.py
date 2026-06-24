"""Tests for the Ralph loop script.

Ralph is deliberately dumb: it reads PROMPT.md, runs the worker under a timeout, prints a line, and
loops. It does not install hooks, verify the gate, or record runs — `set -eu` simply propagates the
worker's exit code and stops the loop on any failure. The install/gate-active behavior that used to
live here now lives in the `ralph` CLI and is tested in test_cli.py / test_integration.py.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RALPH = REPO_ROOT / "harness" / "ralph.sh"


def write_executable(path: Path, text: str) -> None:
    """Write an executable test helper script."""
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def fake_timeout(path: Path) -> None:
    """A fake timeout that records its duration arg (proving the default) then runs the worker."""
    write_executable(path, '#!/bin/sh\nprintf "%s\\n" "$1" >> timeout-secs\nshift\nexec "$@"\n')


def run_ralph(
    tmp_path: Path,
    worker: Path,
    ralph_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run Ralph in a temp repo with a fake timeout that delegates to the worker."""
    (tmp_path / "PROMPT.md").write_text("do the most important thing\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # Shadow both: ralph prefers gtimeout, which exists for real on dev machines and would otherwise
    # run instead of our recorder.
    fake_timeout(bin_dir / "timeout")
    fake_timeout(bin_dir / "gtimeout")
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    command = [str(RALPH), *(ralph_args or []), str(worker)]
    return subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False, env=env)


def test_usage_fails_when_agent_command_is_missing(tmp_path: Path) -> None:
    """The worker command is required."""
    result = subprocess.run([str(RALPH)], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert "defaults: max_iterations=2" in result.stderr


def test_missing_timeout_binary_fails_clearly(tmp_path: Path) -> None:
    """Ralph fails before work when no timeout command is available."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    result = subprocess.run(
        [str(RALPH), "worker"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": str(bin_dir)},
    )
    assert result.returncode == 2
    assert "need gtimeout or timeout" in result.stderr


def test_loop_passes_prompt_and_completes(tmp_path: Path) -> None:
    """Ralph feeds PROMPT.md (plus the iteration marker) to the worker and completes."""
    worker = tmp_path / "worker.sh"
    write_executable(worker, "#!/bin/sh\ncat > received-prompt.txt\nexit 0\n")
    result = run_ralph(tmp_path, worker, ["1", "1"])
    assert result.returncode == 0
    assert (tmp_path / "received-prompt.txt").read_text(encoding="utf-8") == (
        "do the most important thing\n\nRALPH_ITERATION=1/1\n"
    )
    assert "completed 1 iteration(s)" in result.stderr


def test_default_iterations_are_two_when_omitted(tmp_path: Path) -> None:
    """Omitting the numbers runs two loops with a 20-minute (1200s) per-iteration timeout."""
    worker = tmp_path / "worker.sh"
    write_executable(worker, "#!/bin/sh\nexit 0\n")
    result = run_ralph(tmp_path, worker, [])
    assert result.returncode == 0
    assert "iteration 1/2" in result.stderr
    assert "iteration 2/2" in result.stderr
    assert "completed 2 iteration(s)" in result.stderr
    # The fake timeout recorded its duration arg, so we can prove the 20-min default without waiting.
    assert (tmp_path / "timeout-secs").read_text(encoding="utf-8") == "1200\n1200\n"


def test_nonzero_worker_exit_propagates_and_stops(tmp_path: Path) -> None:
    """A worker abort propagates its exit code (set -e) and stops Ralph before the next iteration."""
    worker = tmp_path / "worker.sh"
    write_executable(worker, "#!/bin/sh\nexit 7\n")
    result = run_ralph(tmp_path, worker, ["2", "1"])
    assert result.returncode == 7
    assert "iteration 1/2" in result.stderr
    assert "iteration 2/2" not in result.stderr
    assert "completed" not in result.stderr


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


def test_gtimeout_is_preferred(tmp_path: Path) -> None:
    """Ralph prefers gtimeout when both timeout commands exist."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_timeout(bin_dir / "timeout")
    write_executable(
        bin_dir / "gtimeout",
        "#!/bin/sh\nprintf 'gtimeout\\n' > used-timeout\nshift\nexec \"$@\"\n",
    )
    worker = tmp_path / "worker.sh"
    write_executable(worker, "#!/bin/sh\nexit 0\n")
    (tmp_path / "PROMPT.md").write_text("x\n", encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    subprocess.run(
        [str(RALPH), "1", "1", str(worker)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert (tmp_path / "used-timeout").read_text(encoding="utf-8") == "gtimeout\n"


def test_worker_args_pass_through_verbatim_without_substitution(tmp_path: Path) -> None:
    """Ralph does no token substitution: worker args (e.g. a literal {{PROMPT}}) reach the worker
    byte-for-byte. The prompt is delivered only on stdin, never injected into argv."""
    (tmp_path / "PROMPT.md").write_text("do the most important thing\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_timeout(bin_dir / "timeout")
    worker = tmp_path / "worker.sh"
    # Record argv on one channel and stdin on another, so we can prove where the prompt went.
    write_executable(worker, '#!/bin/sh\nprintf "%s\\n" "$@" > args.txt\ncat > stdin.txt\n')
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
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


def test_missing_prompt_stops_the_loop(tmp_path: Path) -> None:
    """A missing PROMPT.md fails the iteration instead of recording a successful empty run."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_timeout(bin_dir / "timeout")
    worker = tmp_path / "worker.sh"
    write_executable(worker, "#!/bin/sh\nexit 0\n")
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    result = subprocess.run(
        [str(RALPH), "1", "1", str(worker)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode != 0
    assert "completed" not in result.stderr


def test_zero_iterations_rejected(tmp_path: Path) -> None:
    """Zero iterations is refused instead of reporting a vacuous success."""
    result = subprocess.run(
        [str(RALPH), "0", "worker"], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert result.returncode == 2
    assert "must be >= 1" in result.stderr


def test_zero_minutes_rejected(tmp_path: Path) -> None:
    """Zero minutes is refused (it would disable the per-iteration timeout)."""
    result = subprocess.run(
        [str(RALPH), "1", "0", "worker"], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert result.returncode == 2
    assert "must be >= 1" in result.stderr


def test_ralph_loop_env_reaches_worker(tmp_path: Path) -> None:
    """RALPH_LOOP=1 is exported into the worker's environment — the containment marker."""
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
    fake_timeout(bin_dir / "timeout")
    worker = tmp_path / "worker.sh"
    write_executable(worker, '#!/bin/sh\nprintf "%s\\n" "$@" > args.txt\n')
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    subprocess.run(
        [str(RALPH), "1", "1", str(worker), "--flag", "a b"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert (tmp_path / "args.txt").read_text(encoding="utf-8") == "--flag\na b\n"
