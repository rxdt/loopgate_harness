"""Tests for the preflight/gate checks and loop containment (harness.gate)."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from unittest.mock import Mock, call

import pytest

from harness import gate
from harness.tests.conftest import REPO_ROOT, fake_popen


def stage(repo: Path, name: str, content: str) -> None:
    """Write a file inside the repo and stage it."""
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    gate.run_git(["add", name], repo)


def wipe_history(repo: Path) -> None:
    """Rewind the repo to having no commits at all."""
    gate.run_git(["checkout", "--orphan", "initial"], repo)
    gate.run_git(["rm", "-qr", "--cached", "."], repo)


def stage_a_bad_iteration(repo: Path) -> None:
    """Stage everything an agent might try in one iteration, honest work and cheating alike."""
    stage(repo, "pyproject.toml", "[tool.harness]\n")
    gate.run_git(["commit", "-q", "-m", "add config"], repo)
    gate.run_git(["rm", "-q", "pyproject.toml"], repo)
    stage(repo, "src/feature.py", "value = 2\n")
    stage(repo, "harness/gate.py", "FORBIDDEN_PATTERNS = []\n")
    stage(repo, "harness/evil.py", "_ejected = 1  # noqa\n")
    stage(repo, "harness/tests/test_gate.py", "def test_x() -> None:\n    pass\n")
    stage(repo, "PyProject.TOML", "[tool.harness]\n")
    stage(repo, ".github/workflows/ci.yml", "jobs:\n  gate:\n    steps: []\n")
    stage(repo, ".githooks/pre-commit", "#!/bin/sh\nexit 0\n")
    stage(repo, "src/sloppy.py", "import os  # noqa\n")
    stage(repo, "release.sh", "git commit --no-verify -m ship\n")
    stage(repo, "src/named.py", "_bad = 1\n")
    stage(repo, "src/clean.py", "good = 1\n")
    (repo / "src" / "clean.py").write_text("_never_staged = 1\n", encoding="utf-8")


def git_process(repo: Path, *args: str, loop: bool = True) -> subprocess.CompletedProcess[str]:
    """Run Git in a disposable repo without inherited Git state."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    if not loop:
        env.pop("RALPH_LOOP", None)
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False, env=env)


def get_logged_calls_and_clear(repo: Path) -> list[object]:
    """Read and clear complete calls recorded by the temporary harness."""
    log = repo / "harness.calls"
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    log.write_text("", encoding="utf-8")
    return calls


@pytest.mark.parametrize("real_hook_repo", [("pre-commit",)], indirect=True)
@pytest.mark.parametrize(
    ("exit_code", "lands"),
    [pytest.param(0, True, id="passing"), pytest.param(1, False, id="blocking")],
)
def test_pre_commit_hook_dispatches_preflight_and_controls_commit(
    exit_code: int, lands: bool, real_hook_repo: Path
) -> None:
    """The tracked pre-commit hook runs the recorded preflight and owns the commit verdict."""
    stage(real_hook_repo, "feature.py", "value = 1\n")
    (real_hook_repo / "harness.exit").write_text(str(exit_code), encoding="utf-8")
    before = gate.run_git(["rev-parse", "HEAD"], real_hook_repo).strip()
    result = git_process(real_hook_repo, "commit", "-q", "-m", "exercise pre-commit")
    after = gate.run_git(["rev-parse", "HEAD"], real_hook_repo).strip()
    assert (
        result.returncode == 0,
        get_logged_calls_and_clear(real_hook_repo),
        after != before,
        gate.run_git(["show", "--name-only", "--format=", "HEAD"], real_hook_repo).splitlines(),
    ) == (
        lands,
        [{"arguments": ["preflight"], "RALPH_LOOP": "1"}],
        lands,
        ["feature.py"] if lands else [".gitignore", "README.md", "README.template.md"],
    )


@pytest.mark.parametrize("real_hook_repo", [("pre-commit", "pre-push")], indirect=True)
def test_pre_push_hook_dispatches_gate_and_blocks_push(real_hook_repo: Path) -> None:
    """The tracked pre-push hook invokes gate and prevents a local remote ref update on failure."""
    stage(real_hook_repo, "pushable.py", "value = 1\n")
    commit = git_process(real_hook_repo, "commit", "-q", "-m", "pushable work")
    assert commit.returncode == 0, commit.stderr
    get_logged_calls_and_clear(real_hook_repo)

    remote = real_hook_repo.parent / "origin.git"
    assert git_process(real_hook_repo, "init", "--bare", "-q", str(remote)).returncode == 0
    gate.run_git(["remote", "add", "origin", str(remote)], real_hook_repo)
    (real_hook_repo / "harness.exit").write_text("1", encoding="utf-8")
    push = git_process(real_hook_repo, "push", "-q", "origin", "HEAD:main")
    remote_ref = git_process(
        real_hook_repo,
        "--git-dir",
        str(remote),
        "rev-parse",
        "--verify",
        "refs/heads/main",
    )

    assert (
        push.returncode != 0,
        get_logged_calls_and_clear(real_hook_repo),
        remote_ref.returncode == 0,
    ) == (True, [{"arguments": ["gate"], "RALPH_LOOP": "1"}], False)


@pytest.mark.parametrize("real_hook_repo", [("prepare-commit-msg",)], indirect=True)
def test_prepare_commit_msg_hook_rejects_empty_agent_then_accepts_staged_work(
    real_hook_repo: Path,
) -> None:
    """The hook rejects an empty agent commit, then accepts the agent's staged work."""
    before = gate.run_git(["rev-parse", "HEAD"], real_hook_repo).strip()
    agent_empty = git_process(
        real_hook_repo,
        "commit",
        "--allow-empty",
        "--no-verify",
        "-q",
        "-m",
        "agent empty",
    )
    assert agent_empty.returncode != 0
    assert get_logged_calls_and_clear(real_hook_repo) == [
        {
            "arguments": ["prepare-commit-msg", "COMMIT_EDITMSG", "message"],
            "RALPH_LOOP": "1",
        }
    ]
    assert gate.run_git(["rev-parse", "HEAD"], real_hook_repo).strip() == before
    assert "Empty commit detected" in agent_empty.stdout + agent_empty.stderr

    stage(real_hook_repo, "feature.py", "value = 1\n")
    agent_work = git_process(real_hook_repo, "commit", "-q", "-m", "agent work")
    assert agent_work.returncode == 0
    assert get_logged_calls_and_clear(real_hook_repo) == [
        {
            "arguments": ["prepare-commit-msg", "COMMIT_EDITMSG", "message"],
            "RALPH_LOOP": "1",
        }
    ]
    assert gate.run_git(["show", "--name-only", "--format=", "HEAD"], real_hook_repo).splitlines() == [
        "feature.py"
    ]


@pytest.mark.parametrize("real_hook_repo", [("prepare-commit-msg",)], indirect=True)
def test_prepare_commit_msg_hook_allows_human_empty_commit(
    real_hook_repo: Path,
) -> None:
    """The hook does not apply agent containment to a human's empty commit."""
    human_empty = git_process(
        real_hook_repo,
        "commit",
        "--allow-empty",
        "--no-verify",
        "-q",
        "-m",
        "human empty",
        loop=False,
    )

    assert human_empty.returncode == 0
    assert get_logged_calls_and_clear(real_hook_repo) == [
        {
            "arguments": ["prepare-commit-msg", "COMMIT_EDITMSG", "message"],
            "RALPH_LOOP": None,
        }
    ]
    assert gate.run_git(["show", "--name-only", "--format=", "HEAD"], real_hook_repo).splitlines() == []


@pytest.mark.parametrize("real_hook_repo", [("pre-commit", "prepare-commit-msg")], indirect=True)
def test_agent_iteration_is_contained_and_rejected(
    real_hook_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Real commits reject blank, bad, forbidden, and empty attempts before landing only good work."""
    preflight = {"arguments": ["preflight"], "RALPH_LOOP": "1"}
    prepare = {
        "arguments": ["prepare-commit-msg", "COMMIT_EDITMSG", "message"],
        "RALPH_LOOP": "1",
    }
    stage_a_bad_iteration(real_hook_repo)
    get_logged_calls_and_clear(real_hook_repo)
    (real_hook_repo / "harness.real").write_text("preflight\n", encoding="utf-8")
    initial_head = gate.run_git(["rev-parse", "HEAD"], real_hook_repo).strip()

    blank = git_process(real_hook_repo, "commit", "-q", "--no-verify", "--allow-empty-message", "-m", "")
    assert (
        blank.returncode != 0,
        "Commit message is blank" in blank.stdout + blank.stderr,
        gate.run_git(["rev-parse", "HEAD"], real_hook_repo).strip(),
        get_logged_calls_and_clear(real_hook_repo),
    ) == (True, True, initial_head, [prepare])
    message_file = real_hook_repo / ".git" / "COMMIT_EDITMSG"
    message_file.write_text("# generated comment only\n", encoding="utf-8")
    assert gate.prepare_commit_msg(["prepare-commit-msg", str(message_file), "message"]) == 1
    assert "Commit message is blank" in capsys.readouterr().out

    bad = git_process(real_hook_repo, "commit", "-q", "-m", "bad and forbidden work")
    assert (
        bad.returncode != 0,
        gate.run_git(["rev-parse", "HEAD"], real_hook_repo).strip(),
        gate.run_git(["diff", "--cached", "--name-only"], real_hook_repo).splitlines(),
        get_logged_calls_and_clear(real_hook_repo),
        [value in bad.stdout + bad.stderr for value in ("# noqa", "--no-verify", "_bad")],
    ) == (
        True,
        initial_head,
        [
            "release.sh",
            "src/clean.py",
            "src/feature.py",
            "src/named.py",
            "src/sloppy.py",
        ],
        [preflight],
        [True, True, True],
    )
    assert (real_hook_repo / "harness" / "gate.py").exists()

    gate.run_git(
        ["reset", "-q", "HEAD", "--", "release.sh", "src/named.py", "src/sloppy.py"],
        real_hook_repo,
    )
    good = git_process(real_hook_repo, "commit", "-q", "-m", "good work")
    good_head = gate.run_git(["rev-parse", "HEAD"], real_hook_repo).strip()
    assert (
        good.returncode,
        good_head != initial_head,
        gate.run_git(["show", "--name-only", "--format=", "HEAD"], real_hook_repo).splitlines(),
        get_logged_calls_and_clear(real_hook_repo),
    ) == (0, True, ["src/clean.py", "src/feature.py"], [preflight, prepare])
    assert gate.run_git(["show", "HEAD:src/clean.py"], real_hook_repo) == "good = 1\n"

    stage(real_hook_repo, "harness/again.py", "value = 1\n")
    forbidden = git_process(real_hook_repo, "commit", "-q", "-m", "forbidden only")
    assert (
        forbidden.returncode != 0,
        "Empty commit detected" in forbidden.stdout + forbidden.stderr,
        gate.run_git(["rev-parse", "HEAD"], real_hook_repo).strip(),
        gate.run_git(["diff", "--cached", "--name-only"], real_hook_repo).splitlines(),
        get_logged_calls_and_clear(real_hook_repo),
    ) == (True, True, good_head, [], [preflight, prepare])

    empty = git_process(
        real_hook_repo,
        "commit",
        "-q",
        "--allow-empty",
        "--no-verify",
        "-m",
        "empty work",
    )
    assert (
        empty.returncode != 0,
        "Empty commit detected" in empty.stdout + empty.stderr,
        gate.run_git(["rev-parse", "HEAD"], real_hook_repo).strip(),
        get_logged_calls_and_clear(real_hook_repo),
    ) == (True, True, good_head, [prepare])


def test_agent_iteration_that_does_the_work_lands(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], git_repo: Path
) -> None:
    """An honest iteration passes every stage, on an established repo and on a brand new one."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.chdir(git_repo)
    stage(git_repo, "src/feature.py", "value = 2\n")
    stage(git_repo, "docs/notes.md", "Run with `# noqa` to silence the linter.\n")

    (git_repo / ".git" / "COMMIT_EDITMSG").write_text("add the feature\n", encoding="utf-8")
    assert (
        gate.prepare_commit_msg([
            "prepare-commit-msg",
            ".git/COMMIT_EDITMSG",
            "message",
        ])
        == 0
    )
    (git_repo / ".git" / "COMMIT_EDITMSG").write_text("add the feature\n", encoding="utf-8")
    assert gate.prepare_commit_msg(["prepare-commit-msg", ".git/COMMIT_EDITMSG", "commit"]) == 0
    assert gate.run_non_human_checks() == []
    assert gate.run_git(["diff", "--cached", "--name-only"]).splitlines() == [
        "docs/notes.md",
        "src/feature.py",
    ]

    gate.run_git(["commit", "-q", "-m", "add the feature"], git_repo)
    wipe_history(git_repo)
    stage(git_repo, "first.py", "x = 1\n")
    (git_repo / ".git" / "COMMIT_EDITMSG").write_text("first commit\n", encoding="utf-8")
    assert (
        gate.prepare_commit_msg([
            "prepare-commit-msg",
            ".git/COMMIT_EDITMSG",
            "message",
        ])
        == 0
    )
    assert "[COMMIT BLOCKED]" not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("source", "refusal"),
    [
        ("message", ""),
        ("merge", "You cannot use that git command `merge`.\n"),
        ("squash", "You cannot use that git command `squash`.\n"),
        ("rebase", "You cannot use that git command `rebase`.\n"),
        ("reset", "You cannot use that git command `reset`.\n"),
        ("clean", "You cannot use that git command `clean`.\n"),
        ("filter-branch", "You cannot use that git command `filter-branch`.\n"),
    ],
)
def test_agent_cannot_commit_an_empty_iteration(
    source: str,
    refusal: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    git_repo: Path,
) -> None:
    """Nothing staged is nothing done, and rewriting history is not a way to produce work."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.chdir(git_repo)
    empty = "Empty commit detected. Stage real work, Don't use --allow-empty. Or say if you're blocked\n"

    (git_repo / ".git" / "COMMIT_EDITMSG").write_text("did nothing\n", encoding="utf-8")
    assert gate.prepare_commit_msg(["prepare-commit-msg", ".git/COMMIT_EDITMSG", source]) == 1
    assert capsys.readouterr().out == f"PHASE: PRE COMMIT MESSAGE\n{refusal}{empty}\n"

    wipe_history(git_repo)
    (git_repo / ".git" / "COMMIT_EDITMSG").write_text("did nothing\n", encoding="utf-8")
    assert (
        gate.prepare_commit_msg([
            "prepare-commit-msg",
            ".git/COMMIT_EDITMSG",
            "message",
        ])
        == 1
    )
    assert capsys.readouterr().out == f"PHASE: PRE COMMIT MESSAGE\n{empty}\n"
    assert gate.run_non_human_checks() == []


def test_human_running_the_same_commands_is_not_policed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], git_repo: Path
) -> None:
    """The same iteration outside the loop keeps every edit and blocks nothing."""
    monkeypatch.delenv("RALPH_LOOP", raising=False)
    monkeypatch.chdir(git_repo)
    recorder = Mock(return_value="unexpected preference call")
    monkeypatch.setattr(gate, "prefs", recorder)
    stage_a_bad_iteration(git_repo)
    before = gate.run_git(["diff", "--cached", "--name-only"]).splitlines()

    (git_repo / ".git" / "COMMIT_EDITMSG").write_text("", encoding="utf-8")
    assert (
        gate.prepare_commit_msg([
            "prepare-commit-msg",
            ".git/COMMIT_EDITMSG",
            "message",
        ])
        == 0
    )
    assert not capsys.readouterr().out

    calls = fake_popen(monkeypatch)
    assert gate.run_preflight()["fail"] == []
    recorder.assert_not_called()
    assert gate.run_git(["diff", "--cached", "--name-only"]).splitlines() == before
    assert "harness/gate.py" in before
    assert all(env["FORCE_COLOR"] == "1" for _, _, env in calls)
    assert not [key for _, _, env in calls for key in env if key.startswith("GIT_")]


@pytest.mark.parametrize(
    "forbidden_path",
    [
        *(
            pytest.param(f"{directory}blocked.txt", id=f"dir-{directory}")
            for directory in gate.FORBIDDEN_DIRS
        ),
        *(pytest.param(path, id=f"file-{path}") for path in gate.FORBIDDEN_FILES),
    ],
)
def test_every_configured_forbidden_path_is_ejected(
    forbidden_path: str, monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Every forbidden directory and exact file configured in pyproject is removed from the index."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, forbidden_path, "blocked\n")

    assert gate.run_git(["diff", "--cached", "--name-only"]).splitlines() == [forbidden_path]
    assert gate.run_non_human_checks() == []
    assert gate.run_git(["diff", "--cached", "--name-only"]).splitlines() == []


def test_gate_runs_exactly_what_pyproject_configures(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], git_repo: Path
) -> None:
    """Configuration loads verbatim and real process exits are classified correctly."""
    raw_toml = tomllib.loads((REPO_ROOT / "pyproject.toml").read_bytes().decode())["tool"]["harness"]
    assert raw_toml["preflight"] == gate.COMMIT_CHECKS
    assert raw_toml["preflight"] | raw_toml["gate"] == gate.FULL_CHECKS
    monkeypatch.delenv("RALPH_LOOP", raising=False)

    live = gate.run_checks({
        "ruff lint": [sys.executable, "-c", "print('hello from the check')"],
        "pyright types": [sys.executable, "-c", "raise SystemExit(7)"],
        "ruff format": [sys.executable, "-c", "raise SystemExit(1)"],
    })
    assert live == {
        "pass": ["ruff lint"],
        "fail": ["pyright types"],
        "warn": ["ruff format"],
    }
    printed = capfd.readouterr().out
    assert "hello from the check" in printed
    assert "PHASE: RUFF LINT" in printed

    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.setattr(gate, "FULL_CHECKS", {})
    stage(git_repo, "src/mod.py", "_bad = 1\nf = lambda: 0\n")
    assert gate.run_gate() == {
        "pass": [],
        "fail": [
            (
                "problems:\nsrc/mod.py:1: Name '_bad' starts with underscore\nsrc/mod.py:2: Lambda found "
                "hurting readability and adding complexity."
            )
        ],
        "warn": [],
    }


def test_diff_size_counts_added_and_deleted_lines_together(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A rewrite costs its deletions as well as its additions, and they never cancel out."""
    monkeypatch.setenv("RALPH_LOOP", "1")

    assert gate.check_diff_size(["3\t5\tsrc/mod.py"]) == []
    assert "8 lines modified" in capfd.readouterr().out


@pytest.mark.parametrize(
    ("lines", "verdict"),
    [
        pytest.param(gate.WARN_DIFF_LINES - 1, "quiet", id="under-warn"),
        pytest.param(gate.WARN_DIFF_LINES, "advised", id="at-warn"),
        pytest.param(gate.ERROR_DIFF_LINES, "blocked", id="at-cap"),
    ],
)
def test_diff_size_warns_then_blocks_as_the_change_grows(
    lines: int,
    verdict: str,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The configured thresholds advise first and only block once the change reaches the cap."""
    monkeypatch.setenv("RALPH_LOOP", "1")

    problems = gate.check_diff_size([f"{lines}\t0\tsrc/big.py"])
    output = capfd.readouterr().out

    blocking_message = [f"{lines} line diff, max is {gate.ERROR_DIFF_LINES}"]

    assert f"{lines} lines modified" in output
    assert problems == (blocking_message if verdict == "blocked" else [])


def test_diff_size_counts_docs_but_ignores_lockfiles_and_binaries(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """Docs count toward the diff while lockfiles and binaries do not."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    stats = ["5\t0\tnotes.md", "500\t0\tuv.lock", "-\t-\tlogo.png"]

    assert (gate.check_diff_size(stats), capfd.readouterr().out) == (
        [],
        (
            "PHASE: DIFF SIZE\n"
            "5 lines modified\n"
            f"warn at {gate.WARN_DIFF_LINES}\n"
            f"block at {gate.ERROR_DIFF_LINES}\n"
            "Suggestion: Refactor bloat, inline helpers, reduce mis-direction, re-use fixtures, "
            "cut duplication.\n"
        ),
    )


def test_diff_size_measures_the_very_first_commit_of_a_repository(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], git_repo: Path
) -> None:
    """With no HEAD to compare against, the empty tree is the baseline, so nothing escapes unmeasured."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    wipe_history(git_repo)
    filepath = "src/first.py"
    stage(git_repo, filepath, "value = 1\n" * 9)

    assert not gate.run_git(["rev-parse", "--verify", "HEAD"], git_repo, check=False)
    assert gate.run_non_human_checks() == []
    assert "9 lines modified" in capfd.readouterr().out


def test_diff_size_still_measures_a_commit_with_nothing_staged(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], git_repo: Path
) -> None:
    """Sprawl left entirely unstaged is still sprawl: the size check runs before the empty-commit exit."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    filepaths = ["src/mod.py", "mod.py"]
    stage(git_repo, filepaths[0], "value = 1\n")
    gate.run_git(["commit", "-q", "-m", "seed the file"], git_repo)
    # One line already exists, so the rewrite has to be a line longer to add MAX_DIFF_LINES of its own.
    (git_repo / "src" / filepaths[1]).write_text(
        "value = 1\n" * (gate.ERROR_DIFF_LINES + 1), encoding="utf-8"
    )

    problems = gate.run_non_human_checks()
    output = capfd.readouterr().out

    assert "PHASE: EMPTY COMMIT" in output
    assert problems == [f"problems:\n{gate.ERROR_DIFF_LINES} line diff, max is {gate.ERROR_DIFF_LINES}"]


def test_lint_command_keeps_required_flags() -> None:
    """The fast lint command remains Ruff's fixing-aware repository-wide check."""
    command = gate.COMMIT_CHECKS["lint"]
    assert command == ["ruff", "check", "--no-cache", "--show-fixes", "."]


def test_type_check_keeps_machine_readable_output() -> None:
    """Pyright retains stable JSON output for callers that parse its diagnostics."""
    command = gate.FULL_CHECKS["types"]

    assert command[0] == "pyright"
    assert "--outputjson" in command


def test_security_scan_keeps_blocking_rules() -> None:
    """Semgrep stays blocking, scans the repository, and includes code and secret rules."""
    command = gate.FULL_CHECKS["security"]
    configs = [command[index + 1] for index, item in enumerate(command[:-1]) if item == "--config"]

    assert command[:2] == ["semgrep", "scan"]
    assert "--error" in command
    assert configs == ["auto", "p/secrets"]
    assert not any(item == "--exclude" or item.startswith("--exclude=") for item in command)
    assert command[-1] == "."


def test_pytest_gate_keeps_full_coverage_threshold() -> None:
    """The configured test gate continues to require complete measured coverage."""
    command = gate.FULL_CHECKS["pytest"]

    assert {"--cov", "--cov-report=term-missing", "--cov-fail-under=100"} <= set(command)


def test_preflight_flags_preferences_break_under_loop(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Preflight preserves every preference failure alongside a failing configured check."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.chdir(git_repo)
    assert gate.prefs is not None
    recorder = Mock(wraps=gate.prefs)
    monkeypatch.setattr(gate, "prefs", recorder)
    source = "def _bad(*args):\n    transform = lambda item: item\n    return transform(*args)\n"
    stage(git_repo, "src/mod.py", source)
    fake_popen(monkeypatch, fails=[gate.COMMIT_CHECKS["lint"]])

    result = gate.run_preflight()

    assert {
        "preferences": recorder.call_args_list,
        "result": result,
        "staged_paths": gate.run_git(["diff", "--cached", "--name-only"], git_repo).splitlines(),
        "staged_source": gate.run_git(["show", ":src/mod.py"], git_repo),
    } == {
        "preferences": [call("src/mod.py", source)],
        "result": {
            "pass": ["pylint", "format", "complexipy"],
            "fail": [
                "lint",
                (
                    "problems:\n"
                    "src/mod.py:1: Name '_bad' starts with underscore\n"
                    "src/mod.py:1: '*args'/'**kwargs' hide the function signature, use explicit parameters\n"
                    "src/mod.py:2: Lambda found hurting readability and adding complexity.\n"
                    "src/mod.py:3: Dynamic '*' call hides positional arguments; pass explicit arguments"
                ),
            ],
            "warn": [],
        },
        "staged_paths": ["src/mod.py"],
        "staged_source": source,
    }


def test_preferences_read_each_staged_blob_not_the_worktree(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Preferences parse each staged Python blob and ignore later working-tree edits."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.chdir(git_repo)
    stage(git_repo, "src/a.py", "_staged_only = 1\n")
    stage(git_repo, "src/b.py", "b = 2\n")
    (git_repo / "src/a.py").write_text("working_tree_clean = 3\n", encoding="utf-8")
    (git_repo / "src/b.py").write_text("_also_not_staged = 4\n", encoding="utf-8")

    assert (
        gate.check_for_preferences(),
        [
            (git_repo / "src/a.py").read_text(encoding="utf-8"),
            (git_repo / "src/b.py").read_text(encoding="utf-8"),
        ],
    ) == (
        ["src/a.py:1: Name '_staged_only' starts with underscore", ""],
        ["working_tree_clean = 3\n", "_also_not_staged = 4\n"],
    )


def test_bad_patterns_and_preferences_report_separate_violations(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """The scanners accept diff lines and file paths while preserving both violations."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.chdir(git_repo)
    assert gate.prefs is not None
    recorder = Mock(wraps=gate.prefs)
    monkeypatch.setattr(gate, "prefs", recorder)
    source = "def _bad(*args):\n    return 1  # noqa\n"
    stage(git_repo, "src/mod.py", source)

    problems = gate.check_for_bad_patterns()
    problems.extend(filter(None, gate.check_for_preferences()))

    assert {
        "preferences": recorder.call_args_list,
        "problems": problems,
        "staged_paths": gate.run_git(["diff", "--cached", "--name-only"], git_repo).splitlines(),
    } == {
        "preferences": [call("src/mod.py", source)],
        "problems": [
            "'# noqa' line: return 1  # noqa",
            (
                "src/mod.py:1: Name '_bad' starts with underscore\n"
                "src/mod.py:1: '*args'/'**kwargs' hide the function signature, use explicit parameters"
            ),
        ],
        "staged_paths": ["src/mod.py"],
    }


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("notes.txt", "_bad = 1\n"),
        ("data.json", "{not: valid python (((\n"),
        ("app.js", "console.log('pass');\n"),
    ],
)
def test_preferences_only_ever_read_python(
    name: str, content: str, monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Non-Python is never parsed as Python, whether by suffix, by deletion, or by project language."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    recorder = Mock(return_value="unexpected preference call")
    monkeypatch.setattr(gate, "prefs", recorder)
    stage(git_repo, name, content)
    assert gate.run_non_human_checks() == []
    recorder.assert_not_called()

    monkeypatch.setattr(gate, "languages", ["rb"])
    stage(git_repo, "app.rb", "def foo; end\n")
    assert gate.check_for_preferences() == []
    recorder.assert_not_called()

    monkeypatch.setattr(gate, "languages", ["py"])
    stage(git_repo, "src/gone.py", "value = 1\n")
    gate.run_git(["commit", "-q", "-m", "add gone"], git_repo)
    gate.run_git(["rm", "-q", "src/gone.py"], git_repo)
    assert gate.run_non_human_checks() == []
    recorder.assert_not_called()


def test_deleting_preferences_disables_the_check_not_the_gate(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """preferences.py is meant to be deletable, so the gate keeps running without it."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "src/mod.py", "_bad = 1\n")
    assert gate.check_for_preferences() == ["src/mod.py:1: Name '_bad' starts with underscore"]

    monkeypatch.setattr(gate, "prefs", None)
    monkeypatch.setitem(sys.modules, "preferences.preferences", None)
    importlib.reload(gate)
    monkeypatch.setattr(gate, "REPO_ROOT", git_repo)
    monkeypatch.setattr(gate, "COMMIT_CHECKS", {})
    monkeypatch.setattr(gate, "FULL_CHECKS", {})
    assert gate.prefs is None

    assert gate.run_preflight() == {"pass": [], "fail": [], "warn": []}
    assert gate.run_gate() == {"pass": [], "fail": [], "warn": []}

    monkeypatch.undo()
    importlib.reload(gate)
    assert gate.prefs is not None
