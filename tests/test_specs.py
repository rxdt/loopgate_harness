from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_MARKERS = (
    "DELETE OR REPLACE",
    "_Describe the product",
    "_The single most important",
    "_First slice",
)


def test_base_spec_is_active_and_concrete() -> None:
    spec = (ROOT / "specs" / "base.md").read_text(encoding="utf-8")

    assert "PRIORITY 1 (active)" in spec
    assert all(marker not in spec for marker in TEMPLATE_MARKERS)
    assert "Acceptance Signals" in spec


def test_prompt_drives_loop_work_instead_of_one_off_diagnostics() -> None:
    prompt = (ROOT / "PROMPT.md").read_text(encoding="utf-8")

    assert "Report on your current config/permission settings" not in prompt
    assert "Do NOT edit, create, or commit" not in prompt
    assert "Read `specs/`" in prompt
    assert "harness gate" in prompt
    assert "Keep history linear on the current branch" in prompt
    assert "behavior-focused names and docstrings" in prompt


def test_prompt_uses_current_cli_command_names() -> None:
    prompt = (ROOT / "PROMPT.md").read_text(encoding="utf-8")

    assert "uv run ralph" not in prompt
    assert "ralph gate" not in prompt
    assert "ralph verify" not in prompt
    assert "harness verify" not in prompt
