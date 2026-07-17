"""Tests for the context-health file writer.

Boundary scores are solved from the pressure_risk equation against a 600K
window: peak 427,500 lands exactly on APPROACHED (75) and must flip to
wrap_up; 425,000 lands at 74 and must stay ok.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.contextrot import RotScore
from harness.health import HEALTH_FILE, health_status, write_health


def score(peak: int) -> RotScore:
    """A Claude score with the given peak against the 600K default window.

    Args:
        peak: Peak live tokens.

    Returns:
        The score.
    """
    return RotScore("claude-opus-4-8", peak, 600_000, "model-default", True, False)


def test_health_file_constant_points_into_dot_harness() -> None:
    """The published location is .harness/context-health.json."""
    assert Path(".harness") / "context-health.json" == HEALTH_FILE


def test_status_ok_below_approached() -> None:
    """Risk 74, one below the APPROACHED cut, publishes ok."""
    assert health_status(score(425_000)) == "ok"


def test_status_wrap_up_at_approached_boundary() -> None:
    """Risk exactly 75 (the APPROACHED cut) publishes wrap_up."""
    assert health_status(score(427_500)) == "wrap_up"


def test_write_health_publishes_status_risk_and_zone(tmp_path: Path) -> None:
    """The file holds exactly the three protocol fields for the score."""
    path = tmp_path / "context-health.json"
    write_health(path, score(540_000))
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "status": "wrap_up",
        "pressure_risk": 100,
        "zone": "red",
    }


def test_write_health_ends_with_newline(tmp_path: Path) -> None:
    """The payload is newline-terminated for clean cat/tail output."""
    path = tmp_path / "context-health.json"
    write_health(path, score(60_000))
    assert path.read_text(encoding="utf-8").endswith("}\n")


def test_write_health_creates_missing_parent_directory(tmp_path: Path) -> None:
    """Writing into an absent .harness directory creates it."""
    path = tmp_path / ".harness" / "context-health.json"
    write_health(path, score(60_000))
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "ok"


def test_write_health_leaves_no_temp_file(tmp_path: Path) -> None:
    """The atomic-rename staging file never survives the write."""
    path = tmp_path / "context-health.json"
    write_health(path, score(60_000))
    assert list(tmp_path.iterdir()) == [path]


def test_write_health_overwrites_previous_verdict(tmp_path: Path) -> None:
    """A second write fully replaces the first; the flip back to ok is readable."""
    path = tmp_path / "context-health.json"
    write_health(path, score(540_000))
    write_health(path, score(60_000))
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "status": "ok",
        "pressure_risk": 0,
        "zone": "green",
    }
