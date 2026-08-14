"""Native Windows behavioral tests for the PowerShell Ralph runner."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from harness.tests.conftest import REPO_ROOT

RALPH = REPO_ROOT / "harness" / "ralph.ps1"
POWERSHELL = shutil.which("powershell.exe")


def run_ralph(
    tmp_path: Path, arguments: list[str], prompt: str = "do the most important thing"
) -> subprocess.CompletedProcess[str]:
    """Run the PowerShell loop in a temporary working directory."""
    assert POWERSHELL is not None, "Windows CI requires Windows PowerShell"
    env = os.environ.copy()
    env["RALPH_PROMPT"] = prompt
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-File", str(RALPH), *arguments],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=15,
    )


def write_worker(tmp_path: Path, source: str) -> Path:
    """Write a Python worker used by a real PowerShell child process."""
    worker = tmp_path / "worker.py"
    worker.write_text(source, encoding="utf-8")
    return worker


def test_two_iterations_pass_prompt_marker_and_environment(tmp_path: Path) -> None:
    """Explicit limits run twice and pass the prompt, marker, and containment variable."""
    worker = write_worker(
        tmp_path,
        "from pathlib import Path\n"
        "import os\n"
        "import sys\n"
        "count_path = Path('count.txt')\n"
        "count = int(count_path.read_text() if count_path.exists() else '0') + 1\n"
        "count_path.write_text(str(count))\n"
        "Path(f'prompt-{count}.txt').write_text(sys.stdin.read(), encoding='utf-8')\n"
        "Path(f'loop-{count}.txt').write_text(os.environ['RALPH_LOOP'], encoding='utf-8')\n",
    )

    result = run_ralph(tmp_path, ["2", "20", sys.executable, str(worker)])

    assert result.returncode == 0
    assert (tmp_path / "count.txt").read_text(encoding="utf-8") == "2"
    for iteration in (1, 2):
        assert (tmp_path / f"prompt-{iteration}.txt").read_text(encoding="utf-8") == (
            f"do the most important thing\n\nRALPH_ITERATION={iteration}/2\n"
        )
        assert (tmp_path / f"loop-{iteration}.txt").read_text(encoding="utf-8") == "1"
    # stdout is the run receipt `harness run` saves as .jsonl, so it must match ralph.sh's contract
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert [event["type"] for event in events] == ["ralph", "ralph", "ralph"]
    assert [event.get("iteration") for event in events] == [1, 2, None]
    assert events[-1]["completed"] == 2
    assert all(event["max_minutes"] == 20 for event in events)


def test_explicit_one_iteration_completes(tmp_path: Path) -> None:
    """An explicit one-iteration loop runs the worker once."""
    worker = write_worker(tmp_path, "import sys\nsys.stdin.read()\n")

    result = run_ralph(tmp_path, ["1", "1", sys.executable, str(worker)])

    assert result.returncode == 0
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert (events[0]["iteration"], events[-1]["completed"]) == (1, 1)
    assert all(event["max_minutes"] == 1 for event in events)


def test_worker_arguments_are_preserved_exactly(tmp_path: Path) -> None:
    """Literal -p, model flags, and spaced values reach the worker as distinct unchanged arguments."""
    worker = write_worker(
        tmp_path,
        "from pathlib import Path\n"
        "import json\n"
        "import sys\n"
        "Path('args.json').write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
        "sys.stdin.read()\n",
    )
    worker_args = ["-p", "--model", "claude opus", "value with spaces", 'quote"inside', "trailing\\"]

    result = run_ralph(tmp_path, ["1", "1", sys.executable, str(worker), *worker_args])

    assert result.returncode == 0
    assert json.loads((tmp_path / "args.json").read_text(encoding="utf-8")) == worker_args


def test_command_without_additional_arguments_runs(tmp_path: Path) -> None:
    """A worker executable with no argv tail does not trigger an invalid PowerShell array slice."""
    result = run_ralph(tmp_path, ["1", "1", "sort.exe"])

    assert result.returncode == 0


def test_nonzero_worker_exit_propagates_and_stops(tmp_path: Path) -> None:
    """The first worker failure reaches the caller and prevents later iterations."""
    result = run_ralph(
        tmp_path,
        ["2", "1", sys.executable, "-c", "import sys; sys.stdin.read(); raise SystemExit(7)"],
    )

    assert result.returncode == 7
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert [event["iteration"] for event in events] == [1]


def test_fractional_timeout_returns_124_and_stops_process_tree(tmp_path: Path) -> None:
    """A positive fractional minute gives a fast real timeout with GNU-compatible status 124."""
    result = run_ralph(
        tmp_path,
        ["2", "0.001", sys.executable, "-c", "import sys, time; sys.stdin.read(); time.sleep(30)"],
    )

    assert result.returncode == 124
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert [event["iteration"] for event in events] == [1]
