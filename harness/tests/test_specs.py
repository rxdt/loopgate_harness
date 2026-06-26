from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_base_spec_is_active_and_concrete() -> None:
    """Delete or update if you change specs"""
    spec = (ROOT / "specs" / "base.md").read_text(encoding="utf-8")

    assert "PRIORITY" in spec
    assert "Acceptance Criteria" in spec
    assert len(spec.splitlines()) < 101


def test_prompt_drives_loop_work_instead_of_one_off_diagnostics() -> None:
    prompt = (ROOT / "PROMPT.md").read_text(encoding="utf-8")

    assert "Report on your current config/permission settings" not in prompt
    assert "Do NOT edit, create, or commit" not in prompt
    assert "Read `specs/`" in prompt
    assert "harness gate" in prompt
    assert "Keep history linear" in prompt
    assert "uv run ralph" not in prompt
    assert "ralph gate" not in prompt
    assert "ralph verify" not in prompt
    assert "harness verify" not in prompt
    assert "harness preflight" in prompt
    assert "harness gate" in prompt
