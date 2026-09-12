"""Read back the JSONL run logs `harness run` writes under `scratchpad/runs`. Read-only: parse, never write.

One file is one run; one line is one event. Agents disagree on words for the same facts — Claude reports run totals
in a `type: "result"` line, Codex in a `type: "turn.completed"` line, with different names for the same numbers —
so events are found by `type`, never by position, and a fact an agent never reported renders as a dash, never a
wrong zero. Ralph's own `type: "ralph"` framing lines wrap every run and carry the two facts every agent logs:
iteration count and wall-clock span.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BLANK = "-"  # a fact the agent never reported: show a dash, not a zero


@dataclass(frozen=True)
class RunRow:
    """One run summarized for display; every field is a display string, BLANK when never reported."""

    when: str
    agent: str
    run: str
    iterations: str
    minutes: str
    tokens_in: str
    tokens_out: str
    cache_read: str
    cache_write: str
    cost_usd: str
    ms: str
    stopped: str
    message: str
    log: str


def iter_logs(runs: Path) -> list[Path]:
    """Every run log under a runs directory, oldest first; empty when the directory does not exist."""
    return sorted(runs.rglob("*.jsonl")) if runs.is_dir() else []


def _json(line: str) -> dict[str, Any]:
    """Event object encoded in one log line, or {} for anything that is not a JSON object."""
    try:
        event = json.loads(line)
    except (ValueError, TypeError):
        return {}
    return event if isinstance(event, dict) else {}


def _value_at(event: dict[str, Any], path: str) -> Any:
    """Value at a dotted path like "usage.input_tokens", or None when any hop is missing."""
    current: Any = event
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _either(result: dict[str, Any], *paths: str) -> str:
    """First reported value among synonymous Claude and Codex field names, as a string, or BLANK."""
    for path in paths:
        value = _value_at(result, path)
        if value is not None:
            return str(value)
    return BLANK


def _number(value: Any, fmt: str) -> str:
    """Value rendered through a numeric format, or BLANK when missing, not a number, or not finite."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return BLANK
    return BLANK if not math.isfinite(number) else fmt.format(number)


def _text(value: Any) -> str:
    """Agent text as a one-line string, or BLANK when the agent gave none."""
    if not isinstance(value, str) or not value.strip():
        return BLANK
    return " ".join(value.split())


def _message(events: list[dict[str, Any]]) -> str:
    """Why the run stopped, in the agent's own words: Claude's result text or Codex's last agent_message."""
    for event in reversed(events):
        if event.get("type") == "result":
            return _text(event.get("result"))
        item = event.get("item")
        if event.get("type") == "item.completed" and isinstance(item, dict) and item.get("type") == "agent_message":
            return _text(item.get("text"))
    return BLANK


def _stamp(value: Any) -> datetime | None:
    """Ralph line timestamp (`YYYY-MM-DDTHH:MM`, local machine time), or None when missing or unreadable.

    Returns:
        The instant pinned to UTC — ralph writes both framing lines in the same local zone, so differences are
        true elapsed time and the UTC label is never displayed.
    """
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _span(started: dict[str, Any], completed: dict[str, Any]) -> str:
    """Whole minutes across ralph's framing lines, or BLANK when the timestamps do not give one."""
    start, end = _stamp(started.get("timestamp")), _stamp(completed.get("timestamp"))
    if start is None or end is None or end < start:
        return BLANK
    return str(round((end - start).total_seconds() / 60))


def _ralph(frames: list[dict[str, Any]]) -> tuple[str, str]:
    """Iterations ran and elapsed minutes from ralph's framing lines: the facts every agent shares.

    Args:
        frames: The log's `type: "ralph"` events in file order.

    Returns:
        iterations: The completed count, or the started iteration while a run has no end line yet.
        minutes: Wall-clock minutes between the first and last framing line.
    """
    if not frames:
        return BLANK, BLANK
    finished = next((event.get("completed") for event in frames if "completed" in event), None)
    started = next((event.get("iteration") for event in frames if "iteration" in event), BLANK)
    return str(started if finished is None else finished), _span(frames[0], frames[-1])


def _totals(events: list[dict[str, Any]]) -> dict[str, Any]:
    """The event carrying an agent's run totals: Claude's result line, else Codex's turn.completed line."""
    for kind in ("result", "turn.completed"):
        found = next((event for event in events if event.get("type") == kind), None)
        if found is not None:
            return found
    return {}


def read_runs(logs: list[Path]) -> list[RunRow]:
    """Summarize each run log in the order given; one unreadable or malformed log is skipped, not fatal.

    Args:
        logs: Run log paths, any order; rows come back in the same order.

    Returns:
        One display row per readable log, facts BLANK where the agent never reported them.
    """
    rows: list[RunRow] = []
    for log in logs:
        try:
            events = [
                event for event in map(_json, log.read_text(encoding="utf-8", errors="replace").splitlines()) if event
            ]
        except OSError:
            continue
        frames = [event for event in events if event.get("type") == "ralph"]
        result = _totals(events)
        iterations, minutes = _ralph(frames)
        started = frames[0] if frames else {}
        rows.append(
            RunRow(
                when=str(_value_at(started, "timestamp") or BLANK),
                agent=log.parent.name,
                run=log.stem,
                iterations=iterations,
                minutes=minutes,
                tokens_in=_either(result, "usage.input_tokens"),
                tokens_out=_either(result, "usage.output_tokens"),
                cache_read=_either(result, "usage.cache_read_input_tokens", "usage.cached_input_tokens"),
                cache_write=_either(result, "usage.cache_creation_input_tokens", "usage.cache_write_input_tokens"),
                cost_usd=_number(_value_at(result, "total_cost_usd"), "${:.2f}"),
                ms=_number(_value_at(result, "duration_ms"), "{:.0f}"),
                stopped=_either(result, "stop_reason"),
                message=_message(events),
                log=str(log),
            )
        )
    return rows
