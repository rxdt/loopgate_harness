"""Tests for the context-rot scorer.

Golden peaks are computed from the real fixture logs in tests/data with
tiktoken o200k_base + the 12K cap, reproducing the researched anchors exactly
(codex 91,639 / 53,141 / 105,217; claude 20,942 exact). Windows are model-keyed
with an agent fallback; every score records which source supplied the window.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import contextrot

DATA = Path(__file__).resolve().parent / "data"


def fixture(name: str) -> str:
    """Read a fixture log's text.

    Args:
        name: File under tests/data.

    Returns:
        The log text.
    """
    return (DATA / name).read_text(encoding="utf-8")


def chars4(text: str) -> int:
    """Coarse chars/4 counter, standing in for a failed tiktoken load.

    Args:
        text: Text to count.

    Returns:
        len(text) // 4.
    """
    return len(text) // 4


def scored(
    agent: str, text: str, *, model: str | None = None, window: int | None = None
) -> contextrot.RotScore:
    """score_log asserting a non-None result.

    Args:
        agent: Harness agent key.
        text: Log text.
        model: Forwarded to score_log.
        window: Forwarded to score_log.

    Returns:
        The RotScore, failing the test if unscoreable.
    """
    score = contextrot.score_log(agent, text, model=model, window=window)
    assert score is not None
    return score


def assistant(**usage: int) -> str:
    """A Claude assistant JSONL record carrying the given usage fields.

    Args:
        **usage: usage-block token fields.

    Returns:
        One JSON line.
    """
    return json.dumps({"type": "assistant", "request_id": "r", "message": {"usage": usage}})


# --------------------------------------------------------------------------- codex goldens

CODEX_GOLDENS = [
    ("codex-0001.jsonl", 91_639, 48, "yellow", "ok"),
    ("codex-0004.jsonl", 53_141, 19, "green", "ok"),
    ("codex-0005.jsonl", 105_217, 58, "orange", "warn"),
]


@pytest.mark.parametrize("golden", CODEX_GOLDENS)
def test_codex_golden_values(golden: tuple[str, int, int, str, str]) -> None:
    """Codex reconstruction reproduces the o200k_base golden peak/risk/zone/gate against 180K."""
    name, peak, risk, zone, gate = golden
    score = scored("codex", fixture(name), model="gpt-5.5")
    assert (score.peak_live_tokens, score.pressure_risk, score.zone, score.gate) == (peak, risk, zone, gate)
    assert (score.effective_window, score.window_source) == (180_000, "model-default")
    assert score.exact is False
    assert score.approx_tokens is False


def test_codex_ordering_holds() -> None:
    """The runs stay ordered 0004 < 0001 < 0005 by peak."""
    peaks = [
        scored("codex", fixture(f"codex-{n}.jsonl"), model="gpt-5.5").peak_live_tokens
        for n in ("0004", "0001", "0005")
    ]
    assert peaks == sorted(peaks)


def test_codex_without_model_uses_agent_default() -> None:
    """No model= still scores, via the agent-default window, flagged as such."""
    score = scored("codex", fixture("codex-0001.jsonl"))
    assert (score.model, score.effective_window, score.window_source) == (None, 180_000, "agent-default")


def test_codex_output_cap_applied_not_command() -> None:
    """The 12K cap bounds tool output only; the command is uncapped."""
    big = "x " * 40_000
    line = json.dumps({
        "type": "item.completed",
        "item": {"type": "command_execution", "command": "ls", "aggregated_output": big},
    })
    assert scored("codex", line, model="gpt-5.5").peak_live_tokens == 1 + contextrot.CODEX_TOOL_OUTPUT_CAP


def test_codex_turn_completed_only_is_unscoreable() -> None:
    """A cumulative-only log must NOT fall back to the uncached proxy."""
    line = json.dumps({
        "type": "turn.completed",
        "usage": {"input_tokens": 9_682_863, "cached_input_tokens": 9_501_312},
    })
    assert contextrot.score_log("codex", line, model="gpt-5.5") is None


def test_codex_skips_non_json_and_unknown_items() -> None:
    """Non-JSON lines and unknown item types contribute nothing."""
    msg = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "hello world"}})
    todo = json.dumps({"type": "item.completed", "item": {"type": "todo_list", "items": ["a"]}})
    text = f"ralph: iteration 1/1\n{msg}\nERROR boom\n{todo}\n"
    assert scored("codex", text, model="gpt-5.5").peak_live_tokens == contextrot.count_tokens_o200k(
        "hello world"
    )


def test_codex_fallback_counter_flags_approx_tokens() -> None:
    """A supplied chars/4 counter marks the score approx and under-counts vs the anchor."""
    score = contextrot.score_log("codex", fixture("codex-0004.jsonl"), model="gpt-5.5", count_tokens=chars4)
    assert score is not None
    assert score.approx_tokens is True
    assert score.peak_live_tokens < 53_141


# --------------------------------------------------------------------------- claude


def test_claude_golden_exact() -> None:
    """Claude peak is the exact three-field sum deduped by request_id; model + 600K read from log."""
    score = scored("claude", fixture("claude-0002.jsonl"))
    assert (score.peak_live_tokens, score.model) == (20_942, "claude-opus-4-8")
    assert (score.pressure_risk, score.zone, score.gate) == (0, "green", "ok")
    assert (score.effective_window, score.window_source, score.exact) == (600_000, "model-default", True)


def test_claude_reads_model_from_log() -> None:
    """The model comes from message.model, resolving a 200K haiku to its 120K window."""
    rec = {
        "type": "assistant",
        "request_id": "r",
        "message": {"model": "claude-haiku-4-5", "usage": {"input_tokens": 10}},
    }
    score = scored("claude", json.dumps(rec))
    assert (score.model, score.effective_window, score.window_source) == (
        "claude-haiku-4-5",
        120_000,
        "model-default",
    )


def test_claude_model_arg_overrides_log() -> None:
    """An explicit model= wins over message.model."""
    score = scored("claude", fixture("claude-0002.jsonl"), model="claude-haiku-4-5")
    assert (score.model, score.effective_window) == ("claude-haiku-4-5", 120_000)


def test_claude_dedupes_by_request_id() -> None:
    """Records sharing a request_id count once; peak is the single live value, not the sum."""
    rec = json.dumps({
        "type": "assistant",
        "request_id": "req_1",
        "message": {"id": "m", "usage": {"input_tokens": 100, "cache_read_input_tokens": 20_000}},
    })
    assert scored("claude", f"{rec}\n{rec}").peak_live_tokens == 20_100


def test_claude_falls_back_to_message_id_then_line() -> None:
    """Without request_id dedup uses message.id, else each record is distinct by line."""
    a = {"type": "assistant", "message": {"id": "m", "usage": {"input_tokens": 10}}}
    b = {"type": "assistant", "message": {"usage": {"input_tokens": 30}}}
    c = {"type": "assistant", "message": {"usage": {"input_tokens": 40}}}
    assert scored("claude", "\n".join(json.dumps(r) for r in (a, b, c))).peak_live_tokens == 40


def test_claude_assistant_without_usage_is_skipped() -> None:
    """An assistant record lacking usage is skipped; a later one still scores."""
    no_usage = json.dumps({"type": "assistant", "message": {"model": "claude-opus-4-8"}})
    assert scored("claude", f"{no_usage}\n{assistant(input_tokens=500)}").peak_live_tokens == 500


def test_claude_no_usage_is_unscoreable() -> None:
    """A log with no assistant usage is unscoreable, never silently healthy."""
    assert contextrot.score_log("claude", '{"type":"system"}') is None


def test_claude_three_field_sum_not_input_tokens_alone() -> None:
    """Cached tokens occupy the window: 2 + 961 + 19527 = 20,490, not 2."""
    score = scored(
        "claude", assistant(input_tokens=2, cache_creation_input_tokens=961, cache_read_input_tokens=19_527)
    )
    assert score.peak_live_tokens == 20_490


def test_blank_lines_are_skipped() -> None:
    """Blank lines are ignored, not treated as parse errors."""
    assert scored("claude", f"\n\n{assistant(input_tokens=42)}\n   \n").peak_live_tokens == 42


# --------------------------------------------------------------------------- windows, provenance, agents


def test_explicit_window_overrides_and_flags_configured() -> None:
    """A caller window wins over the lookup, is flagged configured, and raises risk."""
    rec = json.dumps({
        "type": "assistant",
        "request_id": "r",
        "message": {"model": "claude-opus-4-8", "usage": {"input_tokens": 60_000}},
    })
    default = scored("claude", rec)
    narrow = scored("claude", rec, window=120_000)
    assert (narrow.effective_window, narrow.window_source) == (120_000, "configured")
    assert narrow.pressure_risk > default.pressure_risk


def test_unknown_model_falls_back_to_agent_default() -> None:
    """An unrecognized model resolves to the agent default, flagged agent-default."""
    score = scored("codex", fixture("codex-0001.jsonl"), model="gpt-6-unreleased")
    assert (score.effective_window, score.window_source) == (180_000, "agent-default")


def test_zone_and_gate_never_contradict() -> None:
    """Zone and gate read the same pressure_risk, so red never pairs with an ok/warn gate."""
    for peak in range(0, 200_001, 5_000):
        score = scored("claude", assistant(input_tokens=peak), window=180_000)
        if score.zone == "red":
            assert score.gate in {"approached", "passed"}
        if score.gate == "ok":
            assert score.zone in {"green", "yellow"}


def test_unsupported_agent_returns_none() -> None:
    """agy/copilot are not scoreable; score_log returns None."""
    assert contextrot.score_log("agy", fixture("claude-0002.jsonl")) is None
    assert contextrot.score_log("copilot", fixture("claude-0002.jsonl")) is None


# --------------------------------------------------------------------------- incremental tracker


def fold(tracker: contextrot.RotTracker, text: str) -> contextrot.RotScore | None:
    """Feed a log to a tracker line-by-line and return the last score observed.

    Args:
        tracker: The tracker under test.
        text: Full log text to stream one line at a time.

    Returns:
        The final score, or None if the log never became scoreable.
    """
    score: contextrot.RotScore | None = None
    for line in text.splitlines():
        score = tracker.observe(line) or score
    return score


def test_tracker_claude_fold_equals_batch_score() -> None:
    """Streaming the claude fixture line-by-line lands on exactly score_log's score."""
    text = fixture("claude-0002.jsonl")
    final = fold(contextrot.RotTracker("claude"), text)
    assert final == contextrot.score_log("claude", text)


def test_tracker_codex_fold_equals_batch_score() -> None:
    """Streaming the codex fixture line-by-line lands on exactly score_log's score."""
    text = fixture("codex-0005.jsonl")
    final = fold(contextrot.RotTracker("codex", model="gpt-5.5"), text)
    assert final == contextrot.score_log("codex", text, model="gpt-5.5")


def test_tracker_crossed_warn_fires_exactly_once() -> None:
    """crossed(WARN) is False below the cut, True once at first crossing, then False again."""
    tracker = contextrot.RotTracker("claude")
    tracker.observe(assistant(input_tokens=100_000))  # risk 2, well below WARN
    assert tracker.crossed(contextrot.WARN) is False
    tracker.observe(assistant(input_tokens=320_000))  # risk 51, first crossing
    assert tracker.crossed(contextrot.WARN) is True
    tracker.observe(assistant(input_tokens=400_000))  # risk 69, still past the cut
    assert tracker.crossed(contextrot.WARN) is False


def test_tracker_crossed_second_threshold_fires_independently() -> None:
    """After WARN has fired, APPROACHED still fires exactly once at its own cut."""
    tracker = contextrot.RotTracker("claude")
    tracker.observe(assistant(input_tokens=320_000))  # risk 51: past WARN, below APPROACHED
    assert tracker.crossed(contextrot.WARN) is True
    assert tracker.crossed(contextrot.APPROACHED) is False
    tracker.observe(assistant(input_tokens=500_000))  # risk 91: past APPROACHED
    assert tracker.crossed(contextrot.APPROACHED) is True
    assert tracker.crossed(contextrot.APPROACHED) is False


def test_tracker_unsupported_agent_never_scores_or_crosses() -> None:
    """agy is not scoreable: observe stays None and crossed stays False, whatever streams in."""
    tracker = contextrot.RotTracker("agy")
    assert tracker.observe(assistant(input_tokens=500_000)) is None
    assert tracker.crossed(contextrot.WARN) is False


def test_tracker_non_json_line_returns_current_score() -> None:
    """A non-JSON line returns None before any signal and the standing score after one."""
    tracker = contextrot.RotTracker("claude")
    assert tracker.observe("ralph: iteration 1/1") is None
    tracker.observe(assistant(input_tokens=42))
    later = tracker.observe("ERROR boom")
    assert later is not None
    assert later.peak_live_tokens == 42


def test_tracker_codex_unscoreable_until_first_item() -> None:
    """Codex observe stays None over non-item records; the first item.completed starts the sum."""
    tracker = contextrot.RotTracker("codex", model="gpt-5.5")
    assert tracker.observe('{"type":"turn.completed","usage":{"input_tokens":5}}') is None
    msg = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "hello world"}})
    score = tracker.observe(msg)
    assert score is not None
    assert score.peak_live_tokens == contextrot.count_tokens_o200k("hello world")


def test_tracker_claude_model_discovered_mid_stream_updates_window() -> None:
    """Window resolution is lazy: agent-default until the log names a model, model-default after."""
    tracker = contextrot.RotTracker("claude")
    before = tracker.observe(assistant(input_tokens=10))
    assert before is not None
    assert (before.model, before.effective_window, before.window_source) == (None, 600_000, "agent-default")
    haiku = {
        "type": "assistant",
        "request_id": "r2",
        "message": {"model": "claude-haiku-4-5", "usage": {"input_tokens": 20}},
    }
    after = tracker.observe(json.dumps(haiku))
    assert after is not None
    assert (after.model, after.effective_window, after.window_source) == (
        "claude-haiku-4-5",
        120_000,
        "model-default",
    )


# --------------------------------------------------------------------------- formatting + verdict wrapper


def test_format_codex_line() -> None:
    """The Codex line carries (~est), separators, and window provenance."""
    line = contextrot.format_rot_score(scored("codex", fixture("codex-0005.jsonl"), model="gpt-5.5"))
    assert line == (
        "context-rot: ORANGE pressureRisk=58 pressure=58% "
        "peak=105,217/180,000 win=model-default gate=warn (~est)"
    )


def test_format_claude_line() -> None:
    """The Claude line carries (exact) and green/ok at low pressure."""
    line = contextrot.format_rot_score(scored("claude", fixture("claude-0002.jsonl")))
    assert line == (
        "context-rot: GREEN pressureRisk=0 pressure=3% "
        "peak=20,942/600,000 win=model-default gate=ok (exact)"
    )


def test_format_approx_and_unscoreable() -> None:
    """approx-tokens rides inside the marker; None renders the unscoreable line."""
    approx = contextrot.score_log("codex", fixture("codex-0004.jsonl"), model="gpt-5.5", count_tokens=chars4)
    assert "(~est approx-tokens)" in contextrot.format_rot_score(approx)
    assert contextrot.format_rot_score(None) == "context-rot: unscoreable (no token usage in log)"


def test_rot_verdict_reads_and_passes_model(tmp_path: Path) -> None:
    """rot_verdict reads the file and forwards model= (Codex needs it)."""
    claude_log = tmp_path / "0002-claude.jsonl"
    claude_log.write_text(fixture("claude-0002.jsonl"), encoding="utf-8")
    assert "win=model-default gate=ok (exact)" in contextrot.rot_verdict("claude", claude_log)
    codex_log = tmp_path / "0005-codex.jsonl"
    codex_log.write_text(fixture("codex-0005.jsonl"), encoding="utf-8")
    assert "(~est)" in contextrot.rot_verdict("codex", codex_log, model="gpt-5.5")


def test_rot_verdict_unreadable_log(tmp_path: Path) -> None:
    """A missing log yields an explicit unscoreable line, never a crash."""
    missing = tmp_path / "gone.jsonl"
    assert (
        contextrot.rot_verdict("codex", missing, model="gpt-5.5")
        == f"context-rot: unscoreable (log not readable: {missing})"
    )


def test_rot_verdict_unsupported_agent_is_empty(tmp_path: Path) -> None:
    """For an unsupported agent, rot_verdict returns '' so run() prints nothing."""
    log = tmp_path / "0001-agy.jsonl"
    log.write_text(fixture("claude-0002.jsonl"), encoding="utf-8")
    assert not contextrot.rot_verdict("agy", log)


def test_rot_verdict_supported_but_no_usage(tmp_path: Path) -> None:
    """A supported agent whose log lacks usage yields the explicit unscoreable line."""
    log = tmp_path / "0003-codex.jsonl"
    log.write_text('{"type":"turn.completed","usage":{"input_tokens":5}}', encoding="utf-8")
    assert (
        contextrot.rot_verdict("codex", log, model="gpt-5.5")
        == "context-rot: unscoreable (no token usage in log)"
    )
