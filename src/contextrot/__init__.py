"""Offline context-rot scoring for agent run logs.

A drop-in library: score any agent's JSONL log (Claude stream-json or Codex exec)
for context-window pressure, live or at the boundary. Depends only on the log
format, not on any harness -- import it, point it at a log, read the verdict.

    from contextrot import RotTracker, score_log, rot_verdict

The harness computes the score; the agent never does. See scorer.py for why.
"""

from __future__ import annotations

from contextrot.scorer import (
    APPROACHED,
    CODEX_TOOL_OUTPUT_CAP,
    PASSED,
    ROT_ONSET,
    ROT_SATURATION,
    WARN,
    WINDOW_BY_AGENT,
    WINDOW_BY_MODEL,
    RotScore,
    RotTracker,
    TokenCounter,
    count_tokens_o200k,
    format_rot_score,
    rot_verdict,
    score_log,
)

__all__ = [
    "APPROACHED",
    "CODEX_TOOL_OUTPUT_CAP",
    "PASSED",
    "ROT_ONSET",
    "ROT_SATURATION",
    "WARN",
    "WINDOW_BY_AGENT",
    "WINDOW_BY_MODEL",
    "RotScore",
    "RotTracker",
    "TokenCounter",
    "count_tokens_o200k",
    "format_rot_score",
    "rot_verdict",
    "score_log",
]
