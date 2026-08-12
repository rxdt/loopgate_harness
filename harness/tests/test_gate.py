"""Tests for the preflight/gate checks and loop containment (harness.gate)."""

from __future__ import annotations

import json
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, call

import pytest
import tomlkit as tomllib
from click import unstyle
from rich.console import Console

from harness import gate
from harness.gate import Gate, gates
from harness.tests.conftest import REPO_ROOT, fake_popen
from mutation import check_mutmut

WARNING_THRESHOLD = round(gates().settings["error_diff_lines"] * 0.75)


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
    stage(repo, "docs/notes.md", "Run with `# noqa` to silence the linter.\n")
    stage(repo, "src/sloppy.py", "import os  # noqa\n")
    stage(repo, "release.sh", "git commit --no-verify -m ship\n")
    stage(repo, "src/named.py", "_bad = 1\n")
    stage(repo, "src/clean.py", "good = 1\n")
    (repo / "src" / "clean.py").write_text("_never_staged = 1\n", encoding="utf-8")


def git_process(repo: Path, args: list[str], loop: bool = True) -> subprocess.CompletedProcess[str]:
    """Run Git in a disposable repo without inherited Git state."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    if not loop:
        env.pop("RALPH_LOOP", None)
    command = ["git"]
    command.extend(args)
    return subprocess.run(command, cwd=repo, capture_output=True, text=True, check=False, env=env)


def get_logged_calls_and_clear(repo: Path) -> list[object]:
    """Read and clear complete calls recorded by the temporary harness."""
    log = repo / "harness.calls"
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    log.write_text("", encoding="utf-8")
    return calls


def assert_resolver_recovery(case_name: str, original_record: str, stderr: str) -> None:
    """Check the diagnostic for an agent hook that recovered through PATH."""
    expected = {
        "missing": "no recorded harness executable was found",
        "stale": f"recorded harness executable {original_record} is unavailable",
    }.get(case_name)
    if expected is None:
        return
    assert expected in stderr
    assert "Recording fallback" in stderr


@pytest.mark.parametrize("real_hook_repo", [("pre-commit",)], indirect=True)
@pytest.mark.parametrize(
    "case",
    [
        pytest.param(("valid", 0, True, True), id="passing"),
        pytest.param(("valid", 1, False, True), id="blocking"),
        pytest.param(("missing", 0, True, True), id="missing-record"),
        pytest.param(("stale", 0, True, True), id="stale-record"),
        pytest.param(("unavailable", 0, False, False), id="unavailable"),
    ],
)
def test_pre_commit_hook_dispatches_preflight_and_controls_commit(
    case: tuple[str, int, bool, bool], real_hook_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tracked pre-commit hook resolves preflight and owns the commit verdict."""
    recorded = real_hook_repo / ".git" / "harness-path"
    expected_record = recorded.read_text(encoding="utf-8")
    original_record = expected_record.strip()
    if case[0] in {"missing", "stale"}:
        if case[0] == "missing":
            recorded.unlink()
        bin_dir = real_hook_repo / "bin"
        bin_dir.mkdir()
        executable = bin_dir / "harness"
        (real_hook_repo / "recorded-harness").rename(executable)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        expected_record = f"{executable}\n"
    elif case[0] == "unavailable":
        recorded.unlink()
        tool_dir = real_hook_repo / "tool-bin"
        tool_dir.mkdir()
        for name in ("git", "dirname"):
            executable = shutil.which(name)
            assert executable
            (tool_dir / Path(executable).name).symlink_to(executable)
        monkeypatch.setenv("PATH", str(tool_dir))

    stage(real_hook_repo, "feature.py", "value = 1\n")
    (real_hook_repo / "harness.exit").write_text(str(case[1]), encoding="utf-8")
    before = gate.run_git(["rev-parse", "HEAD"], real_hook_repo).strip()
    result = git_process(real_hook_repo, ["commit", "-q", "-m", "exercise pre-commit"])

    assert (
        result.returncode == 0,
        get_logged_calls_and_clear(real_hook_repo) if case[3] else [],
        gate.run_git(["rev-parse", "HEAD"], real_hook_repo).strip() != before,
        gate.run_git(["show", "--name-only", "--format=", "HEAD"], real_hook_repo).splitlines(),
    ) == (
        case[2],
        [{"arguments": ["preflight"], "RALPH_LOOP": "1"}] if case[3] else [],
        case[2],
        ["feature.py"] if case[2] else [".gitignore", "README.md", "README.template.md"],
    )
    if case[0] == "unavailable":
        assert not recorded.exists()
        assert result.stderr == "\nloopgate: hooks are not installed. Run 'harness install' in this repo.\n"
    else:
        # Git Bash records `command -v` hits as MSYS paths (/c/Users/...); compare tail, not drive
        recorded_path = Path(recorded.read_text(encoding="utf-8").strip())
        assert recorded_path.parts[-3:] == Path(expected_record.strip()).parts[-3:]
        assert_resolver_recovery(case[0], original_record, result.stderr)


@pytest.mark.parametrize("real_hook_repo", [("pre-commit",)], indirect=True)
@pytest.mark.parametrize(
    "case",
    [
        pytest.param((WARNING_THRESHOLD, WARNING_THRESHOLD, True, None), id="at-warning-threshold"),
        pytest.param((WARNING_THRESHOLD + 1, WARNING_THRESHOLD + 1, True, "WARNED"), id="warn"),
        pytest.param(
            (gates().settings["error_diff_lines"], gates().settings["error_diff_lines"], True, "WARNED"),
            id="at-error-threshold",
        ),
        pytest.param(
            (gates().settings["error_diff_lines"] + 1, WARNING_THRESHOLD, True, None),
            id="unstaged-lines-ignored",
        ),
    ],
)
def test_pre_commit_hook_warns_then_blocks_on_staged_diff_size(
    case: tuple[int, int, bool, str | None], real_hook_repo: Path
) -> None:
    """The real hook applies the review thresholds only to staged changes."""
    total, staged_lines, lands, verdict = case
    (real_hook_repo / "harness.real").write_text("preflight\n", encoding="utf-8")
    stage(real_hook_repo, "notes.txt", "staged line\n" * staged_lines)
    unstaged_lines = total - staged_lines
    if unstaged_lines:
        (real_hook_repo / "README.md").write_text(
            "seed\n" + "unstaged line\n" * unstaged_lines, encoding="utf-8"
        )
    before = gate.run_git(["rev-parse", "HEAD"], real_hook_repo).strip()

    result = git_process(real_hook_repo, ["commit", "-q", "-m", f"{total} line iteration"])
    output = result.stdout + result.stderr
    after = gate.run_git(["rev-parse", "HEAD"], real_hook_repo).strip()

    assert result.returncode == (0 if lands else 1)
    assert (after != before) is lands
    assert f"{staged_lines} lines of code modified" in output
    assert get_logged_calls_and_clear(real_hook_repo) == [{"arguments": ["preflight"], "RALPH_LOOP": "1"}]
    if verdict is None:
        assert "WARNED" not in output
        assert "FAILED" not in output
    else:
        assert verdict in output


@pytest.mark.parametrize("real_hook_repo", [("pre-commit", "pre-push")], indirect=True)
def test_pre_push_hook_dispatches_gate_and_blocks_push(real_hook_repo: Path) -> None:
    """The tracked pre-push hook invokes gate and prevents a local remote ref update on failure."""
    stage(real_hook_repo, "pushable.py", "value = 1\n")
    commit = git_process(real_hook_repo, ["commit", "-q", "-m", "pushable work"])
    assert commit.returncode == 0, commit.stderr
    get_logged_calls_and_clear(real_hook_repo)

    remote = real_hook_repo.parent / "origin.git"
    assert git_process(real_hook_repo, ["init", "--bare", "-q", str(remote)]).returncode == 0
    gate.run_git(["remote", "add", "origin", str(remote)], real_hook_repo)
    (real_hook_repo / "harness.exit").write_text("1", encoding="utf-8")
    push = git_process(real_hook_repo, ["push", "-q", "origin", "HEAD:main"])
    remote_ref = git_process(
        real_hook_repo, ["--git-dir", str(remote), "rev-parse", "--verify", "refs/heads/main"]
    )

    assert (push.returncode != 0, get_logged_calls_and_clear(real_hook_repo), remote_ref.returncode == 0) == (
        True,
        [{"arguments": ["gate"], "RALPH_LOOP": "1"}],
        False,
    )


@pytest.mark.parametrize("real_hook_repo", [("prepare-commit-msg",)], indirect=True)
def test_prepare_commit_msg_hook_rejects_empty_agent_then_accepts_staged_work(real_hook_repo: Path) -> None:
    """The hook rejects an empty agent commit, then accepts the agent's staged work."""
    before = gate.run_git(["rev-parse", "HEAD"], real_hook_repo).strip()
    agent_empty = git_process(
        real_hook_repo, ["commit", "--allow-empty", "--no-verify", "-q", "-m", "agent empty"]
    )
    assert agent_empty.returncode != 0
    assert get_logged_calls_and_clear(real_hook_repo) == [
        {"arguments": ["prepare-commit-msg", "COMMIT_EDITMSG", "message"], "RALPH_LOOP": "1"}
    ]
    assert gate.run_git(["rev-parse", "HEAD"], real_hook_repo).strip() == before
    assert "Empty commit detected" in agent_empty.stdout + agent_empty.stderr

    stage(real_hook_repo, "feature.py", "value = 1\n")
    agent_work = git_process(real_hook_repo, ["commit", "-q", "-m", "agent work"])
    assert agent_work.returncode == 0
    assert get_logged_calls_and_clear(real_hook_repo) == [
        {"arguments": ["prepare-commit-msg", "COMMIT_EDITMSG", "message"], "RALPH_LOOP": "1"}
    ]
    assert gate.run_git(["show", "--name-only", "--format=", "HEAD"], real_hook_repo).splitlines() == [
        "feature.py"
    ]

    # githooks(5): the hook's first parameter is always the message file; a plain
    # `git commit` (no -m/-t/merge/squash/amend) passes no source argument at all.
    stage(real_hook_repo, "plain.py", "plain = 1\n")
    git_process(real_hook_repo, ["-c", "core.editor=true", "commit", "-q"])
    assert get_logged_calls_and_clear(real_hook_repo) == [
        {"arguments": ["prepare-commit-msg", "COMMIT_EDITMSG"], "RALPH_LOOP": "1"}
    ]


@pytest.mark.parametrize("real_hook_repo", [("prepare-commit-msg",)], indirect=True)
def test_prepare_commit_msg_hook_allows_human_empty_commit(real_hook_repo: Path) -> None:
    """A human bypasses harness resolution entirely and can create an empty commit."""
    recorded = real_hook_repo / ".git" / "harness-path"
    recorded.unlink()
    before = gate.run_git(["rev-parse", "HEAD"], real_hook_repo).strip()
    human_empty = git_process(
        real_hook_repo, ["commit", "--allow-empty", "--no-verify", "-q", "-m", "human empty"], loop=False
    )

    assert (
        human_empty.returncode,
        human_empty.stdout,
        human_empty.stderr,
        gate.run_git(["rev-parse", "HEAD"], real_hook_repo).strip() != before,
        gate.run_git(["show", "--name-only", "--format=", "HEAD"], real_hook_repo).splitlines(),
        (real_hook_repo / "harness.calls").exists(),
        recorded.exists(),
    ) == (0, "", "", True, [], False, False)


@pytest.mark.parametrize("real_hook_repo", [("pre-commit", "prepare-commit-msg")], indirect=True)
def test_agent_iteration_is_contained_and_rejected(
    real_hook_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Real commits reject blank, bad, forbidden, and empty attempts before landing only good work."""
    preflight = {"arguments": ["preflight"], "RALPH_LOOP": "1"}
    prepare = {"arguments": ["prepare-commit-msg", "COMMIT_EDITMSG", "message"], "RALPH_LOOP": "1"}
    stage_a_bad_iteration(real_hook_repo)
    get_logged_calls_and_clear(real_hook_repo)
    (real_hook_repo / "harness.real").write_text("preflight\n", encoding="utf-8")
    initial_head = gate.run_git(["rev-parse", "HEAD"], real_hook_repo).strip()

    blank = git_process(real_hook_repo, ["commit", "-q", "--no-verify", "--allow-empty-message", "-m", ""])
    assert (
        blank.returncode != 0,
        "Commit message is blank" in blank.stdout + blank.stderr,
        gate.run_git(["rev-parse", "HEAD"], real_hook_repo).strip(),
        get_logged_calls_and_clear(real_hook_repo),
    ) == (True, True, initial_head, [prepare])
    message_file = real_hook_repo / ".git" / "COMMIT_EDITMSG"
    message_file.write_text("\n\n# generated comment only\n", encoding="utf-8")
    assert gates().prepare_commit_msg(["prepare-commit-msg", str(message_file), "message"]) == 1
    assert capsys.readouterr().out == (
        "PHASE: PREPARE-COMMIT-MESSAGE\n"
        "Commit message is blank. Provide an informative message with your agent ID.\n\n"
    )

    bad = git_process(real_hook_repo, ["commit", "-q", "-m", "bad and forbidden work"])
    assert (
        bad.returncode != 0,
        gate.run_git(["rev-parse", "HEAD"], real_hook_repo).strip(),
        gate.run_git(["diff", "--cached", "--name-only"], real_hook_repo).splitlines(),
        get_logged_calls_and_clear(real_hook_repo),
        [value in bad.stdout + bad.stderr for value in ("# noqa", "--no-verify", "_bad")],
    ) == (
        True,
        initial_head,
        ["docs/notes.md", "release.sh", "src/clean.py", "src/feature.py", "src/named.py", "src/sloppy.py"],
        [preflight],
        [True, True, True],
    )
    assert "[dim green]" in bad.stdout + bad.stderr
    assert (real_hook_repo / "harness" / "gate.py").exists()

    gate.run_git(
        ["reset", "-q", "HEAD", "--", "docs/notes.md", "release.sh", "src/named.py", "src/sloppy.py"],
        real_hook_repo,
    )
    good = git_process(real_hook_repo, ["commit", "-q", "-m", "good work"])
    good_head = gate.run_git(["rev-parse", "HEAD"], real_hook_repo).strip()
    assert (
        good.returncode,
        good_head != initial_head,
        gate.run_git(["show", "--name-only", "--format=", "HEAD"], real_hook_repo).splitlines(),
        get_logged_calls_and_clear(real_hook_repo),
    ) == (0, True, ["src/clean.py", "src/feature.py"], [preflight, prepare])
    assert gate.run_git(["show", "HEAD:src/clean.py"], real_hook_repo) == "good = 1\n"

    stage(real_hook_repo, "harness/again.py", "value = 1\n")
    forbidden = git_process(real_hook_repo, ["commit", "-q", "-m", "forbidden only"])
    assert (
        forbidden.returncode != 0,
        "Empty commit detected" in forbidden.stdout + forbidden.stderr,
        gate.run_git(["rev-parse", "HEAD"], real_hook_repo).strip(),
        gate.run_git(["diff", "--cached", "--name-only"], real_hook_repo).splitlines(),
        get_logged_calls_and_clear(real_hook_repo),
    ) == (True, True, good_head, [], [preflight, prepare])

    empty = git_process(real_hook_repo, ["commit", "-q", "--allow-empty", "--no-verify", "-m", "empty work"])
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

    (git_repo / ".git" / "COMMIT_EDITMSG").write_text("add the feature\n", encoding="utf-8")
    assert gates().prepare_commit_msg(["prepare-commit-msg", ".git/COMMIT_EDITMSG", "message"]) == 0
    (git_repo / ".git" / "COMMIT_EDITMSG").write_text("add the feature\n", encoding="utf-8")
    assert gates().prepare_commit_msg(["prepare-commit-msg", ".git/COMMIT_EDITMSG"]) == 0
    monkeypatch.setattr(gates(), "commit_checks", {})
    assert (gates().run_preflight(), gate.run_git(["diff", "--cached", "--name-only"]).splitlines()) == (
        {"pass": ["mutmut"], "fail": [], "warn": []},
        ["src/feature.py"],
    )

    gate.run_git(["commit", "-q", "-m", "add the feature"], git_repo)
    wipe_history(git_repo)
    stage(git_repo, "first.py", "x = 1\n")
    (git_repo / ".git" / "COMMIT_EDITMSG").write_text("first commit\n", encoding="utf-8")
    assert gates().prepare_commit_msg(["prepare-commit-msg", ".git/COMMIT_EDITMSG", "message"]) == 0
    assert "[COMMIT BLOCKED]" not in capsys.readouterr().out

    git_calls: list[tuple[list[str], bool | None]] = []

    def record(args: list[str], check: bool = True) -> str:
        git_calls.append((args, check))
        return "abc123\n" if args[0] == "rev-parse" else "first.py\n"

    monkeypatch.setattr(gate, "run_git", record)
    assert gates().prepare_commit_msg(["prepare-commit-msg", ".git/COMMIT_EDITMSG"]) == 0
    assert git_calls == [
        (["rev-parse", "--verify", "HEAD"], False),
        (["diff-index", "--cached", "--name-only", "HEAD"], True),
    ]


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
    empty = "Empty commit detected. Stage real work, Don't use --allow-empty. Say if you're blocked\n"

    (git_repo / ".git" / "COMMIT_EDITMSG").write_text("did nothing\n", encoding="utf-8")
    assert gates().prepare_commit_msg(["prepare-commit-msg", ".git/COMMIT_EDITMSG", source]) == 1
    assert capsys.readouterr().out == f"PHASE: PREPARE-COMMIT-MESSAGE\n{refusal}{empty}\n"

    wipe_history(git_repo)
    (git_repo / ".git" / "COMMIT_EDITMSG").write_text("did nothing\n", encoding="utf-8")
    assert gates().prepare_commit_msg(["prepare-commit-msg", ".git/COMMIT_EDITMSG", "message"]) == 1
    assert capsys.readouterr().out == f"PHASE: PREPARE-COMMIT-MESSAGE\n{empty}\n"

    blank = "Commit message is blank. Provide an informative message with your agent ID.\n"
    (git_repo / ".git" / "COMMIT_EDITMSG").write_text("", encoding="utf-8")
    assert gates().prepare_commit_msg(["prepare-commit-msg", ".git/COMMIT_EDITMSG", "message"]) == 1
    assert capsys.readouterr().out == f"PHASE: PREPARE-COMMIT-MESSAGE\n{empty}{blank}\n"


def test_human_running_the_same_commands_is_not_policed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], git_repo: Path
) -> None:
    """The same iteration outside the loop keeps every edit and blocks nothing."""
    monkeypatch.delenv("RALPH_LOOP", raising=False)
    monkeypatch.chdir(git_repo)
    recorder = Mock(return_value="unexpected preference call")
    monkeypatch.setattr(gate, "prefs", recorder)
    stage_a_bad_iteration(git_repo)
    files_staged_before_preflight = gate.run_git(["diff", "--cached", "--name-only"]).splitlines()

    (git_repo / ".git" / "COMMIT_EDITMSG").write_text("", encoding="utf-8")
    assert gates().prepare_commit_msg(["prepare-commit-msg", ".git/COMMIT_EDITMSG", "message"]) == 0
    assert not capsys.readouterr().out

    calls = fake_popen(monkeypatch)
    assert gates().run_preflight()["fail"] == []  # human does not fail
    assert gate.run_git(["diff", "--cached", "--name-only"]).splitlines() == files_staged_before_preflight
    assert "harness/gate.py" in files_staged_before_preflight
    recorder.assert_called()  # human sees the checks against the code
    for command, cwd, env in calls:
        del command
        assert (env["FORCE_COLOR"], env["CLICOLOR_FORCE"], env["SEMGREP_FORCE_COLOR"]) == ("1", "1", "1")
        assert cwd == git_repo
        assert not any(key.startswith("GIT_") for key in env)


@pytest.mark.parametrize(
    "forbidden_path",
    [
        *(
            pytest.param(f"{directory}blocked.txt", id=f"dir-{directory}")
            for directory in gates().forbidden_dirs
            if directory != ".git/"
        ),
        *(pytest.param(path, id=f"file-{path}") for path in gates().forbidden_files),
    ],
)
def test_every_configured_forbidden_path_is_ejected_except_dot_git(
    forbidden_path: str, monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Every forbidden directory and exact file configured in pyproject is removed from the index, except for
    `.git` which never stages files (but is forbidden to be explicit to agents.)"""
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, forbidden_path, "blocked\n")

    assert gate.run_git(["diff", "--cached", "--name-only"]).splitlines() == [forbidden_path]
    monkeypatch.setattr(gates(), "commit_checks", {})
    assert (
        gates().run_preflight(),
        gate.run_git(["diff", "--cached", "--name-only"]).splitlines(),
        ".git/" in gates().forbidden_dirs,
    ) == ({"pass": ["mutmut"], "fail": [], "warn": []}, [], True)


def test_gate_runs_exactly_what_pyproject_configures(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], git_repo: Path
) -> None:
    """A root owns its complete configuration, Git target, command dispatch, and containment results."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    raw_harness_toml = tomllib.loads((REPO_ROOT / "pyproject.toml").read_bytes().decode())["tool"]["harness"]
    configured = Gate(REPO_ROOT)
    assert vars(configured) == {
        "repo_root": REPO_ROOT,
        "settings": raw_harness_toml["settings"],
        "forbidden": raw_harness_toml["FORBIDDEN"],
        "agents": raw_harness_toml["agents"],
        "commit_checks": raw_harness_toml["preflight"],
        "gate_checks": raw_harness_toml["gate"] | raw_harness_toml["preflight"],
        "forbidden_files": tuple(raw_harness_toml["FORBIDDEN"]["FILES"]),
        "forbidden_dirs": tuple(raw_harness_toml["FORBIDDEN"]["DIRS"]),
        "forbidden_patterns": tuple(raw_harness_toml["FORBIDDEN"]["PATTERNS"]),
    }
    assert vars(gates()) == {**vars(configured), "repo_root": git_repo}
    (git_repo / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    fallback = tomllib.loads((REPO_ROOT / "harness" / "temp.pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["harness"]
    assert Gate(git_repo).gate_checks == fallback["preflight"] | fallback["gate"]
    assert Gate(git_repo).settings == fallback["settings"]
    expected_repo_root = Path(
        subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=REPO_ROOT, text=True).strip()
    )
    assert (
        Path(gate.run_git(["rev-parse", "--show-toplevel"]).strip()),
        Path(gate.run_git(["rev-parse", "--show-toplevel"], REPO_ROOT).strip()),
    ) == (git_repo, expected_repo_root)
    monkeypatch.setenv("GIT_DIR", str(git_repo / "no-such-dir"))
    monkeypatch.delenv("RALPH_LOOP", raising=False)
    assert Path(gate.run_git(["rev-parse", "--show-toplevel"]).strip()) == git_repo
    monkeypatch.delenv("GIT_DIR")
    absent = ["rev-parse", "--verify", "refs/heads/absent"]
    assert not gate.run_git(absent, git_repo, check=False)
    with pytest.raises(subprocess.CalledProcessError):
        gate.run_git(absent, git_repo)

    monkeypatch.setattr(
        gate,
        "console",
        Console(force_terminal=True, color_system=None if os.environ.get("RALPH_LOOP") else "auto"),
    )
    live = gates().run_checks({
        "ruff lint": [sys.executable, "-c", "print('hello from the check')"],
        "pyright types": [sys.executable, "-c", "raise SystemExit(7)"],
        "ruff format": [sys.executable, "-c", "raise SystemExit(1)"],
    })
    assert live == {"pass": ["ruff lint", "mutmut"], "fail": ["pyright types"], "warn": ["ruff format"]}
    printed = unstyle(capfd.readouterr().out)
    assert "hello from the check" in printed
    assert "PHASE: RUFF LINT" in printed
    assert "print('hellofromthecheck')" in "".join(printed.split())

    monkeypatch.setenv("RALPH_LOOP", "1")
    plain_console = Console(
        force_terminal=True, color_system=None if os.environ.get("RALPH_LOOP") else "auto"
    )
    monkeypatch.setattr(gate, "console", plain_console)
    monkeypatch.setattr(check_mutmut, "console", plain_console)
    monkeypatch.setattr(gates(), "gate_checks", {})
    stage(
        git_repo,
        "src/mod.py",
        "_bad = 1\n"
        "f = lambda: 0\n"
        "for item in []:\n"
        "    for inner in []:\n"
        "        continue\n"
        "for item in []:\n"
        "    if item:\n"
        "        continue\n"
        "while flag:\n"
        "    if flag:\n"
        "        continue\n"
        "class Pointless:\n"
        "    def only(self):\n"
        "        pass\n"
        "class Based(dict):\n"
        "    pass\n"
        "class Keyed(metaclass=type):\n"
        "    pass\n"
        "class TwoMethods:\n"
        "    def one(self):\n"
        "        pass\n"
        "    def two(self):\n"
        "        pass\n"
        "assert True\n"
        "globals()\n"
        "locals()\n"
        "print(*[1, 2])\n"
        "pairs = [x for x in [] for y in [] if x]\n",
    )
    assert gates().run_gate() == {
        "pass": ["mutmut"],
        "fail": [
            (
                "PREFERENCES IGNORED:\n"
                "src/mod.py:9: 'continue' inside a while loop banned to prevent infinite freezes\n"
                "src/mod.py:12: 'Pointless': no base, decorator, or behavior: use function or Pydantic\n"
                "src/mod.py:24: Lazy test assertion detected\n"
                "src/mod.py:1: Name '_bad' starts with underscore and is not in a class\n"
                "src/mod.py:2: Lambda found hurting readability and adding complexity, "
                "prefer map() or filter()\n"
                "src/mod.py:25: Dynamic injection of memory registry spotted\n"
                "src/mod.py:26: Dynamic injection of memory registry spotted\n"
                "src/mod.py:28: Overly complex comprehension, use a loop or type Set math\n"
                "src/mod.py:5: Overly-nested 'continue' detected inside multiple if/for blocks"
            )
        ],
        "warn": [],
    }
    printed = unstyle(capfd.readouterr().out)
    phase_output = (
        "PHASE: AGENT CHECKS"
        "\nrunning non-human agent checks"
        "\nPHASE: BANNED PATTERNS FOR AGENT"
        "\ncheck for banned patterns in staged files"
        "\nIssues:\nset()"
        "\nPHASE: REPO PREFERENCES"
        "\nchecking repo preferences are respected by agents"
        "\nIssues:"
    )
    diff_size_output = (
        "\nPHASE: DIFF SIZE"
        "\n28 lines of code modified (insertions + deletions in staged files). "
        "Agents get WARN at 75% 375, ERROR at 500.\n"
    )
    assert printed.startswith(phase_output)
    assert diff_size_output in printed
    assert "MUTMUT MUTATION RESULTS" in printed
    assert "Mutation Score:" in printed
    assert "100.0" in printed
    assert "\x1b" not in printed  # agents in the loop get plain text, never ANSI
    assert gate.run_git(["diff-index", "--cached", "--name-only", "HEAD"]) == "src/mod.py\n"

    monkeypatch.chdir(git_repo.parent)
    gates.cache_clear()
    assert gates().repo_root == Path.cwd()
    assert "Run this inside a git repository" in capfd.readouterr().out
    gates.cache_clear()

    monkeypatch.chdir(git_repo)
    gates.cache_clear()
    gate.run_git(["reset", "-q"], git_repo)
    monkeypatch.setitem(gates().settings, "behavior", "warn")
    live = gates().run_checks({
        "ruff lint": [sys.executable, "-c", "print('hello from the check')"],
        "pyright types": [sys.executable, "-c", "raise SystemExit(7)"],
        "ruff format": [sys.executable, "-c", "raise SystemExit(1)"],
    })
    assert live == {"pass": ["ruff lint", "mutmut"], "fail": [], "warn": ["pyright types", "ruff format"]}
    gates.cache_clear()


@pytest.mark.parametrize(
    ("score", "loop", "bucket"),
    [
        pytest.param(gate.MINIMUM_MUTATION_SCORE, False, "pass", id="at-minimum"),
        pytest.param(gate.MINIMUM_MUTATION_SCORE - 0.1, False, "warn", id="human-below"),
        pytest.param(gate.MINIMUM_MUTATION_SCORE - 0.1, True, "fail", id="agent-below"),
    ],
)
def test_mutation_score_uses_the_configured_minimum(
    score: float, loop: bool, bucket: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The score passes at the configured boundary and falls into the caller's failure bucket below it."""
    monkeypatch.setattr(gate, "analyze_mutmut_report_passed", Mock(return_value=score))
    monkeypatch.setattr(Gate, "_run_non_human_checks", Mock())
    if loop:
        monkeypatch.setenv("RALPH_LOOP", "1")
    else:
        monkeypatch.delenv("RALPH_LOOP", raising=False)

    results = gates().run_checks({})

    assert results[bucket] == ["mutmut"]
    assert sum("mutmut" in values for values in results.values()) == 1


def test_diff_size_counts_only_relevant_changed_lines(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], git_repo: Path
) -> None:
    """Count additions and deletions while excluding generated and binary files."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.setattr(gates(), "commit_checks", {})
    stage(git_repo, "src/mod.py", "".join(f"old_{line} = {line}\n" for line in range(WARNING_THRESHOLD)))
    gate.run_git(["commit", "-q", "-m", "seed rewrite"], git_repo)
    stage(git_repo, "src/mod.py", "new = 1\n")

    rewritten = gates().run_preflight()
    rewrite_output = capfd.readouterr().out

    gate.run_git(["commit", "-q", "-m", "rewrite module"], git_repo)
    filtered_lines = WARNING_THRESHOLD + 1
    stage(git_repo, "notes.txt", "note\n" * filtered_lines)
    stage(git_repo, "src/tiny.py", "tiny_one = 1\ntiny_two = 2\n")
    stage(git_repo, "UV.LOCK", "generated\n" * 500)
    (git_repo / "logo.png").write_bytes(b"\0binary")
    gate.run_git(["add", "logo.png"], git_repo)

    filtered = gates().run_preflight()
    filtered_output = capfd.readouterr().out

    rewritten_lines = WARNING_THRESHOLD + 1
    assert (rewritten["pass"], rewritten["fail"], len(rewritten["warn"])) == (["mutmut"], [], 1)
    assert f"{rewritten_lines} lines of code modified" in rewrite_output
    assert (filtered["pass"], filtered["fail"], len(filtered["warn"])) == (["mutmut"], [], 1)
    assert f"{filtered_lines + 2} lines of code modified" in filtered_output


@pytest.mark.parametrize(
    ("lines", "verdict"),
    [
        pytest.param(WARNING_THRESHOLD, "quiet", id="at-warn"),
        pytest.param(WARNING_THRESHOLD + 1, "advised", id="over-warn"),
        pytest.param(gates().settings["error_diff_lines"], "advised", id="at-cap"),
        pytest.param(gates().settings["error_diff_lines"] + 1, "blocked", id="over-cap"),
    ],
)
def test_diff_size_warns_then_blocks_as_the_change_grows(
    lines: int,
    verdict: str,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    git_repo: Path,
) -> None:
    """The configured thresholds advise first and only block once the change reaches the cap."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.setattr(gates(), "commit_checks", {})
    stage(git_repo, "src/big.py", "value = 1\n" * lines)

    results = gates().run_preflight()
    output = capfd.readouterr().out

    message = (
        f"{lines} lines of code modified (insertions + deletions in staged files). "
        f"Agents get WARN at 75% {WARNING_THRESHOLD}, ERROR at {gates().settings['error_diff_lines']}."
    )
    advisory = message + (
        "\nRefactor bloat, reduce mis-direction, re-use fixtures, cut duplication, slim down code. "
        "More code does not mean good code."
    )
    expected: dict[str, list[str]] = {"pass": ["mutmut"], "fail": [], "warn": []}
    if verdict != "quiet":
        expected["fail" if verdict == "blocked" else "warn"].append(advisory)

    assert (results, message in output, "PHASE: DIFF SIZE" in output) == (expected, True, True)


def test_diff_size_measures_the_very_first_commit_of_a_repository(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], git_repo: Path
) -> None:
    """With no HEAD to compare against, the empty tree is the baseline, so nothing escapes unmeasured."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    wipe_history(git_repo)
    filepath = "src/first.py"
    lines = WARNING_THRESHOLD + 1
    stage(git_repo, filepath, "value = 1\n" * lines)

    assert not gate.run_git(["rev-parse", "--verify", "HEAD"], git_repo, check=False)
    monkeypatch.setattr(gates(), "commit_checks", {})
    results = gates().run_preflight()
    output = capfd.readouterr().out

    assert (results["pass"], results["fail"], len(results["warn"])) == (["mutmut"], [], 1)
    assert f"{lines} lines of code modified" in output


def test_diff_size_ignores_changes_with_nothing_staged(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], git_repo: Path
) -> None:
    """Unstaged changes do not contribute to a commit's diff size."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    filepaths = ["src/mod.py", "mod.py"]
    stage(git_repo, filepaths[0], "value = 1\n")
    gate.run_git(["commit", "-q", "-m", "seed the file"], git_repo)
    (git_repo / "src" / filepaths[1]).write_text(
        "value = 1\n" * (gates().settings["error_diff_lines"] + 2), encoding="utf-8"
    )

    monkeypatch.setattr(gates(), "commit_checks", {})
    results = gates().run_preflight()
    output = capfd.readouterr().out

    assert results == {"pass": ["mutmut"], "fail": [], "warn": []}
    assert "PHASE: EMPTY COMMIT" in output
    assert "PHASE: DIFF SIZE" not in output


def test_lint_command_keeps_required_flags() -> None:
    """The fast lint command remains Ruff's fixing-aware repository-wide check."""
    command = gates().commit_checks["lint"]
    assert command == ["ruff", "check", "--no-cache", "--show-fixes", "."]


def test_type_check_keeps_machine_readable_output() -> None:
    """Pyright retains stable JSON output for callers that parse its diagnostics."""
    command = gates().gate_checks["types"]

    assert command[0] == "pyright"
    assert "--outputjson" in command


def test_security_scan_keeps_blocking_rules() -> None:
    """Semgrep stays blocking, scans the repository, and includes code and secret rules."""
    command = gates().gate_checks["security"]
    configs = [command[index + 1] for index, item in enumerate(command[:-1]) if item == "--config"]

    assert command[:2] == ["semgrep", "scan"]
    assert "--error" in command
    assert configs == ["auto", "p/secrets"]
    assert not any(item == "--exclude" or item.startswith("--exclude=") for item in command)
    assert command[-1] == "."


def test_pytest_gate_keeps_full_coverage_threshold() -> None:
    """The configured test gate continues to require complete measured coverage."""
    command = gates().gate_checks["test"]

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
    fake_popen(monkeypatch, fails=[gates().commit_checks["lint"]])

    result = gates().run_preflight()

    assert {
        "preferences": recorder.call_args_list,
        "result": result,
        "staged_paths": gate.run_git(["diff", "--cached", "--name-only"], git_repo).splitlines(),
        "staged_source": gate.run_git(["show", ":src/mod.py"], git_repo),
    } == {
        "preferences": [call("src/mod.py", source)],
        "result": {
            "pass": ["format", "complexity", "pylint", "mutmut"],
            "fail": [
                "lint",
                (
                    "PREFERENCES IGNORED:\n"
                    "src/mod.py:1: Name '_bad' starts with underscore and is not in a class\n"
                    "src/mod.py:1: '*args', '**kwargs', '*', and '/' hide the function signature, "
                    "use explicit parameters\n"
                    "src/mod.py:2: Lambda found hurting readability and adding complexity, "
                    "prefer map() or filter()\n"
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
    monkeypatch.setattr(gates(), "commit_checks", {})
    stage(git_repo, "src/a.py", "_staged_only = 1\n")
    stage(git_repo, "src/b.py", "b = 2\n")
    (git_repo / "src/a.py").write_text("working_tree_clean = 3\n", encoding="utf-8")
    (git_repo / "src/b.py").write_text("_also_not_staged = 4\n", encoding="utf-8")

    assert (
        gates().run_preflight(),
        [
            (git_repo / "src/a.py").read_text(encoding="utf-8"),
            (git_repo / "src/b.py").read_text(encoding="utf-8"),
        ],
    ) == (
        {
            "pass": ["mutmut"],
            "fail": [
                (
                    "PREFERENCES IGNORED:\n"
                    "src/a.py:1: Name '_staged_only' starts with underscore and is not in a class"
                )
            ],
            "warn": [],
        },
        ["working_tree_clean = 3\n", "_also_not_staged = 4\n"],
    )


def test_bad_patterns_and_preferences_report_separate_violations(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """The scanners accept diff lines and file paths while preserving both violations."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(gates(), "commit_checks", {})
    assert gate.prefs is not None
    recorder = Mock(wraps=gate.prefs)
    monkeypatch.setattr(gate, "prefs", recorder)
    source = "def _bad(*args):\n    return 1  # NoQa\n"
    stage(git_repo, "src/mod.py", source)

    results = gates().run_preflight()

    assert {
        "preferences": recorder.call_args_list,
        "results": results,
        "staged_paths": gate.run_git(["diff", "--cached", "--name-only"], git_repo).splitlines(),
    } == {
        "preferences": [call("src/mod.py", source)],
        "results": {
            "pass": ["mutmut"],
            "fail": [
                "FORBIDDEN FOR AGENT:\nsrc/mod.py: '# noqa'",
                (
                    "PREFERENCES IGNORED:\n"
                    "src/mod.py:1: Name '_bad' starts with underscore and is not in a class\n"
                    "src/mod.py:1: '*args', '**kwargs', '*', and '/' hide the function signature, "
                    "use explicit parameters"
                ),
            ],
            "warn": [],
        },
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
    monkeypatch.setattr(gates(), "commit_checks", {})
    recorder = Mock(return_value="unexpected preference call")
    monkeypatch.setattr(gate, "prefs", recorder)
    stage(git_repo, name, content)
    assert gates().run_preflight() == {"pass": ["mutmut"], "fail": [], "warn": []}
    recorder.assert_not_called()

    monkeypatch.setitem(gates().settings, "languages", ("rb",))
    stage(git_repo, "app.rb", "def foo; end\n")
    assert gates().run_preflight() == {"pass": ["mutmut"], "fail": [], "warn": []}
    recorder.assert_not_called()

    monkeypatch.setitem(gates().settings, "languages", ("py",))
    stage(git_repo, "src/gone.py", "value = 1\n")
    gate.run_git(["commit", "-q", "-m", "add gone"], git_repo)
    gate.run_git(["rm", "-q", "src/gone.py"], git_repo)
    assert gates().run_preflight() == {"pass": ["mutmut"], "fail": [], "warn": []}
    recorder.assert_not_called()


def test_deleting_preferences_disables_the_check_not_the_gate(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A missing preferences module imports cleanly and disables only that optional check."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.setattr(gates(), "commit_checks", {})
    stage(git_repo, "src/mod.py", "_bad = 1\n")
    assert gates().run_preflight() == {
        "pass": ["mutmut"],
        "fail": [
            ("PREFERENCES IGNORED:\nsrc/mod.py:1: Name '_bad' starts with underscore and is not in a class")
        ],
        "warn": [],
    }

    with monkeypatch.context() as missing_preferences:
        missing_preferences.setitem(sys.modules, "preferences.preferences", None)
        imported = runpy.run_path(str(REPO_ROOT / "harness" / "gate.py"))
    assert imported["prefs"] is None

    monkeypatch.setattr(gate, "prefs", None)
    monkeypatch.setattr(gates(), "gate_checks", {})
    assert gate.prefs is None

    assert gates().run_preflight() == {"pass": ["mutmut"], "fail": [], "warn": []}
    assert gates().run_gate() == {"pass": ["mutmut"], "fail": [], "warn": []}
