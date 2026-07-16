"""Context-health file: how the harness tells a running agent to wrap up.

A rotting agent can't judge its own impairment, so the harness scores the log
and publishes the verdict to ``.harness/context-health.json``. The standing
instruction in docs/PROMPT.md tells agents to read it at turn start and wrap
up cleanly when told. Kept dumb on purpose: three fields, atomic replace, no
history -- the file is a signal, not a log.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from harness.contextrot import APPROACHED, RotScore

HEALTH_FILE = Path(".harness") / "context-health.json"

Status = Literal["ok", "wrap_up"]


def health_status(score: RotScore) -> Status:
    """The status to publish for a score.

    Args:
        score: The score to translate.

    Returns:
        "wrap_up" once pressure_risk reaches APPROACHED, else "ok".
    """
    return "wrap_up" if score.pressure_risk >= APPROACHED else "ok"


def write_health(path: Path, score: RotScore) -> None:
    """Publish a score as the context-health file, atomically.

    Written via rename so an agent polling mid-write never reads a torn file.
    Creates the parent directory when missing.

    Args:
        path: Destination file (normally HEALTH_FILE under the repo root).
        score: The score to publish.
    """
    payload = {
        "status": health_status(score),
        "pressure_risk": score.pressure_risk,
        "zone": score.zone,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    tmp.replace(path)
