"""Offline context-rot scoring for finished harness run logs.

The harness scores the log, not the agent: a rotting agent can't reliably judge
its own impairment, and live usage is runtime-written, not agent-visible. Claude
logs exact usage (three-field sum per request); Codex has only a cumulative
total, so its live context is ESTIMATED by re-tokenizing retained items under the
tool-output cap -- never presented as exact. The effective window is a discounted
policy value (advertised windows overstate usable capacity), so it is model-keyed
config with an agent fallback, and every score records which source supplied it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeGuard

import tiktoken

_O200K = tiktoken.get_encoding("o200k_base")

# Discounted from advertised windows; OPTIMISTIC vs the field's 50-65% rule.
# Overridable per run; the resolved source prints beside every score.
WINDOW_BY_MODEL: dict[str, int] = {
    "gpt-5.5": 180_000,  # codex-effective, not the 1.05M API window
    "claude-opus-4-8": 600_000,
    "claude-sonnet-5": 600_000,
    "claude-fable-5": 600_000,
    "claude-haiku-4-5": 120_000,
}
WINDOW_BY_AGENT: dict[str, int] = {"codex": 180_000, "claude": 600_000}
CODEX_TOOL_OUTPUT_CAP = 12_000  # Codex truncates each tool OUTPUT to ~this before context
ROT_ONSET, ROT_SATURATION = 0.15, 0.90  # pressure knees
WARN, APPROACHED, PASSED = 50, 75, 90  # gate cuts, same 0-100 scale as the zones

Zone = Literal["green", "yellow", "orange", "red"]
Gate = Literal["ok", "warn", "approached", "passed"]
WindowSource = Literal["configured", "model-default", "agent-default"]
TokenCounter = Callable[[str], int]


@dataclass(frozen=True)
class RotScore:
    """One log's verdict: a peak measured against a resolved window, plus provenance."""

    model: str | None
    peak_live_tokens: int
    effective_window: int
    window_source: WindowSource
    exact: bool  # True=Claude measured, False=Codex estimate
    approx_tokens: bool  # True when the chars/4 fallback tokenizer was used

    @property
    def pressure(self) -> float:
        """Live fill fraction, clamped to [0, 1]."""
        return min(max(self.peak_live_tokens / self.effective_window, 0.0), 1.0)

    @property
    def pressure_risk(self) -> int:
        """0-100 risk: pressure saturated across the rot knees."""
        span = (self.pressure - ROT_ONSET) / (ROT_SATURATION - ROT_ONSET)
        return round(100 * min(max(span, 0.0), 1.0))

    @property
    def zone(self) -> Zone:
        """Colour band derived from pressure_risk."""
        risk = self.pressure_risk
        return "green" if risk < 25 else "yellow" if risk < WARN else "orange" if risk < APPROACHED else "red"

    @property
    def gate(self) -> Gate:
        """Action band from the same pressure_risk, so it can't contradict the zone."""
        risk = self.pressure_risk
        return (
            "ok"
            if risk < WARN
            else "warn"
            if risk < APPROACHED
            else "approached"
            if risk < PASSED
            else "passed"
        )


def _is_object(value: object) -> TypeGuard[dict[str, object]]:
    """Narrow a json value to an object; JSON keys are always strings."""
    return isinstance(value, dict)


def _dict(value: object) -> dict[str, object]:
    """value as a JSON object, or {}."""
    return value if _is_object(value) else {}


def _int(value: object) -> int:
    """value as an int, or 0."""
    return value if isinstance(value, int) else 0


def count_tokens_o200k(text: str) -> int:
    """Exact o200k_base token count (Codex is OpenAI; the normal path).

    Args:
        text: Text to tokenize.

    Returns:
        The token count.
    """
    return len(_O200K.encode(text))


def _parse_line(line: str) -> dict[str, object] | None:
    """One JSONL line as an object, or None for a blank, non-JSON, or non-object line."""
    try:
        parsed = json.loads(line) if line.strip() else None
    except ValueError:
        parsed = None
    return parsed if _is_object(parsed) else None


def _str(value: object) -> str | None:
    """value as a str, or None."""
    return value if isinstance(value, str) else None


def _live_tokens(usage: dict[str, object]) -> int:
    """input + cache_creation + cache_read; input_tokens alone is only the uncached tail."""
    return (
        _int(usage.get("input_tokens"))
        + _int(usage.get("cache_creation_input_tokens"))
        + _int(usage.get("cache_read_input_tokens"))
    )


def _dedup_key(record: dict[str, object], message: dict[str, object], index: int) -> str:
    """request_id, else message.id, else the line index."""
    return _str(record.get("request_id")) or _str(message.get("id")) or f"line:{index}"


def _codex_item(item: dict[str, object], count: TokenCounter) -> int:
    """Reconstructed live tokens for one item; the 12K cap is on tool OUTPUT only."""
    kind = item.get("type")
    if kind == "command_execution":
        return count(str(item.get("command", ""))) + min(
            count(str(item.get("aggregated_output", ""))), CODEX_TOOL_OUTPUT_CAP
        )
    if kind == "agent_message":
        return count(str(item.get("text", "")))
    if kind == "file_change":
        return count(json.dumps(item.get("changes", [])))
    return 0


def _window(agent: str, model: str | None, override: int | None) -> tuple[int, WindowSource]:
    """Effective window and its provenance: override -> model -> agent default."""
    if override is not None:
        return override, "configured"
    if model in WINDOW_BY_MODEL:
        return WINDOW_BY_MODEL[model], "model-default"
    return WINDOW_BY_AGENT[agent], "agent-default"


class RotTracker:
    """Incremental scorer: feed raw log lines as they stream; peak state is monotonic.

    The same per-record equation as score_log (which folds a whole log through one
    tracker), so batch and live scoring can never disagree.
    """

    def __init__(
        self,
        agent: str,
        *,
        model: str | None = None,
        window: int | None = None,
        count_tokens: TokenCounter | None = None,
    ) -> None:
        """Start tracking one run's stream.

        Args:
            agent: Harness agent key; only "claude" and "codex" are scoreable.
            model: Model id, overriding the log for Claude, required for Codex.
            window: Explicit window override, else resolved from model/agent.
            count_tokens: Codex counter override; defaults to o200k_base.
        """
        self._agent = agent
        self._model = model  # the override if given, else filled from the log's first model field
        self._window_override = window
        self._injected_count = count_tokens  # non-None also flags codex scores approx-tokens
        self._by_key: dict[str, int] = {}  # claude: peak live tokens per unique request
        self._codex_sum: int | None = None  # codex: retained-item sum; None until the first item
        self._fired: set[int] = set()

    def observe(self, line: str) -> RotScore | None:
        """Parse one raw log line, update peak state, and return the current score.

        Returns the current score whenever the accumulated state is scoreable (whether
        or not this line changed it), else None -- so callers can render live state
        after any line, including blank or non-JSON ones.

        Args:
            line: One raw line from the worker's stream.

        Returns:
            The current RotScore, or None while no usable signal has been seen.
        """
        record = _parse_line(line)
        if record is not None and self._agent in WINDOW_BY_AGENT:
            self._ingest(record)
        return self._score()

    def crossed(self, threshold: int) -> bool:
        """True exactly once: the first observe after pressure_risk reaches threshold.

        Edge-detected per threshold value, so each gate cut (WARN, APPROACHED, PASSED)
        fires its own single warning.

        Args:
            threshold: A 0-100 pressure_risk cut.

        Returns:
            True on the first call at-or-past the cut, False before and after.
        """
        score = self._score()
        if score is None or threshold in self._fired or score.pressure_risk < threshold:
            return False
        self._fired.add(threshold)
        return True

    def _ingest(self, record: dict[str, object]) -> None:
        """Fold one parsed record into the peak state; agent decided at construction."""
        if self._agent == "claude":
            self._ingest_claude(record)
        elif record.get("type") == "item.completed":
            item_tokens = _codex_item(_dict(record.get("item")), self._injected_count or count_tokens_o200k)
            self._codex_sum = (self._codex_sum or 0) + item_tokens

    def _ingest_claude(self, record: dict[str, object]) -> None:
        """Track the running max live-token sum per unique request, and the log's model."""
        message = _dict(record.get("message"))
        if record.get("type") != "assistant" or not _is_object(message.get("usage")):
            return
        self._model = self._model or _str(message.get("model"))
        # len(_by_key) only grows on insert, so the fallback key is unique per untagged record.
        key = _dedup_key(record, message, len(self._by_key))
        self._by_key[key] = max(self._by_key.get(key, 0), _live_tokens(_dict(message.get("usage"))))

    def _score(self) -> RotScore | None:
        """The current RotScore; window re-resolved each time (claude's model arrives mid-stream)."""
        if self._agent == "claude":
            peak = max(self._by_key.values()) if self._by_key else None
        else:
            peak = self._codex_sum
        if peak is None:
            return None
        effective, source = _window(self._agent, self._model, self._window_override)
        approx = self._agent == "codex" and self._injected_count is not None
        return RotScore(self._model, peak, effective, source, self._agent == "claude", approx)


def score_log(
    agent: str,
    text: str,
    *,
    model: str | None = None,
    window: int | None = None,
    count_tokens: TokenCounter | None = None,
) -> RotScore | None:
    """Score a finished run log by folding it through a RotTracker. Pure function of the log text.

    Codex must pass ``model`` (its log has none); Claude reads it from the log
    unless overridden.

    Args:
        agent: Harness agent key; only "claude" and "codex" are scoreable.
        text: The full JSONL log text.
        model: Model id, overriding the log for Claude, required for Codex.
        window: Explicit window override, else resolved from model/agent.
        count_tokens: Codex counter override; defaults to o200k_base.

    Returns:
        A RotScore, or None for an unsupported agent or a log with no usable signal.
    """
    tracker = RotTracker(agent, model=model, window=window, count_tokens=count_tokens)
    score: RotScore | None = None
    for line in text.splitlines():
        score = tracker.observe(line) or score
    return score


def format_rot_score(score: RotScore | None) -> str:
    """The single-line ``context-rot:`` verdict; identical format across harness languages.

    Args:
        score: A score, or None for the unscoreable case.

    Returns:
        The operator-facing line.
    """
    if score is None:
        return "context-rot: unscoreable (no token usage in log)"
    marker = "exact" if score.exact else "~est"
    if score.approx_tokens:
        marker += " approx-tokens"
    return (
        f"context-rot: {score.zone.upper()} pressureRisk={score.pressure_risk} "
        f"pressure={round(score.pressure * 100)}% "
        f"peak={score.peak_live_tokens:,}/{score.effective_window:,} "
        f"win={score.window_source} gate={score.gate} ({marker})"
    )


def rot_verdict(agent: str, log_path: Path, *, model: str | None = None) -> str:
    """Read a log and return its verdict line. Never raises; "" for unsupported agents.

    Scoring runs after the worker and must not change its exit code or crash, so
    read failures become an explicit unscoreable line.

    Args:
        agent: Harness agent key.
        log_path: Path to the finished run log.
        model: Model id forwarded to score_log (Codex needs it).

    Returns:
        The verdict line, "" for an unsupported agent, or an unscoreable line on read error.
    """
    if agent not in WINDOW_BY_AGENT:
        return ""
    try:
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return f"context-rot: unscoreable (log not readable: {log_path})"
    return format_rot_score(score_log(agent, text, model=model))
