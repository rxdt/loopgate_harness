"""FAIL CI WHEN MUTMUT'S AGGREGATE EXPORT CONTAINS TEST GAPS.

Mutation testing mutates the source code, e.g. a boolean flips, a constant shifts, a comparison widens.
A mutant that dies proves a test assertion was working on that line. A mutant that SURVIVES is a variant of
source code that shows tests execute (i.e. test coverage is met there) BUT the test is flimsy:
has weak assertions, overly-mock, happy path only, missing edge cases, no real assert in test, weak logic.

i.e. Mutmut tests the tests. Mutmut breaks source then runs test suite. If test fails, the break was noticed,
a mutated source code variant is "killed." If every test passes after mutation the break went unnoticed,
mutant code variant "survived".

Mutmut docs: https://mutmut.readthedocs.io/
    mutmut run       # generate mutants and run the test suite against all source code + its mutants
    mutmut browse    # TUI over the survivors
    mutmut results   # plain-text summary

Config in pyproject.toml [tool.mutmut]

Each mutant variant has a name, e.g. tests.test_file.x_lazy_assert__mutmut_2. Mutmut records mutant as killed
or survived before you inspect, and re-uses variant until source code changes.

CASE:
- best: 100% test coverage, run mutmut -> many mutations, none survive, nothing for you to kill
(tests are strongly sensitive to change)
- good: <100% coverage, run mutmut -> many mutations, none survive, you have to hunt some
(the tests that exist are good but you miss coverage)
- worst: 100% coverage, run mutmut -> many mutations, ALL survive + no easy kills
(there are many low quality tests)
- realistic: run mutmut, some mutants created, some survive, you find some to kill

PROCESS:

1. Run mutmut: mutations appear / are killed
2. Inspect: You look at surviving mutations
3. Test Update: You add or improve test assertions
4. Re-run: You ensure the mutant is killed

5. Updating Source Code
- Dead Code
Sometimes a mutant survives because source code is redundant, e.g. If changing a line doesn't break a test,
ask if that code is needed. Maybe delete the useless code instead of writing tests.
- Surviving Mutants
You do not need to reach zero mutants, e.g. if changing source effects performance negatively, message output
would change, equivalent code swap e.g. i < 10 => i != 10

Locally you can run `mutmut run` to generate mutants. Your test suite must be green.
Then `mutmut export-cicd-stats` to get a report.
Run *this* script to use that report to get a score and badge-capable score for your README.
```
> mutmut run
⠼ Generating mutants
    done in 2457ms (6 files mutated, 7 ignored, 0 unmodified)
⠧ Running stats
    done
⠼ Running clean tests
    done
⠏ Running forced fail test
    done
Running mutation testing
⠼ 1434/1434  🎉 1207 🫥 0  ⏰ 1  🤔 0  🙁 226  🔇 0  🧙 0
5.28 mutations/second
> mutmut export-cicd-stats
Saved CI/CD stats to mutants/mutmut-cicd-stats.json
> python mutation/check_mutmut.py  # writes `mutation-score.json` in the format Shields expects and prints:

───────────────MUTMUT MUTATION RESULTS ───────────────

            killed                          1207
            survived                        226
            total                           1434
            no_tests                        0
            skipped                         0
            suspicious                      0
            timeout                         1
            check_was_interrupted_by_user   0
            segfault                        0
            Mutation Score:                84.2

Add to README:
[![mutation](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/OWNER/REPO/BRANCH/
mutation-score.json)](https://github.com/OWNER/REPO/blob/BRANCH/mutation-score.json)

> git commit && git push
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from rich import print as rprint
from rich.console import Console
from rich.table import Table

console = Console(force_terminal=True, color_system=None if os.environ.get("RALPH_LOOP") else "auto")

MINIMUM_MUTATION_SCORE = 60.0  # increase over time


def update_mutation_score() -> float:
    """Read the Mutmut report and write the latest repository mutation score.

    Returns:
        Latest calculated mutation score.
    """
    stats_report_path = Path("mutants/mutmut-cicd-stats.json")
    badge_score = Path("mutation-score.json")
    if not stats_report_path.exists():
        repo_root = Path(__file__).resolve().parents[1]
        stats_report_path = repo_root / stats_report_path
        badge_score = repo_root / badge_score
    mutation_score = analyze_mutmut_report_passed(str(stats_report_path))
    badge = {"schemaVersion": 1, "label": "mutation", "message": f"{mutation_score:.1f}%", "color": "#177445"}
    badge_score.write_text(json.dumps(badge, indent=4) + "\n", encoding="utf-8")
    return mutation_score


def analyze_mutmut_report_passed(file_path: str = "mutants/mutmut-cicd-stats.json") -> float:
    """Read the mutmut CI JSON results created each Sunday night.

    Args:
        file_path (str): Default filepath to read mutmut run stats from.

    Returns:
        mutation_score: float value, killed + timeout mutants as a% of all

    Raises:
        JSONDecodeError: If the report does not contain valid JSON.
    """
    mutation_score: float = 0.0
    if not Path(file_path).exists():
        rprint(f"[bold red]Mutmut report not at {file_path}[/]:\nRun `mutmut run && mutmut export-cicd-stats`")
        return mutation_score
    data: dict[str, int] = {}
    with Path(file_path).open("r", encoding="utf-8") as fp:
        try:
            data = json.load(fp)
        except json.JSONDecodeError:
            rprint(rf"[red]JSONDecodeError [/]'{file_path}'")
            raise

    total_mutants = data.get("total", 0)
    skipped = data.get("skipped", 0)
    tested_mutants = total_mutants - skipped
    if tested_mutants > 0:
        killed = data.get("killed", 0)
        timeout = data.get("timeout", 0)
        mutation_score = ((killed + timeout) / tested_mutants) * 100

    console.rule("[bold cyan]MUTMUT MUTATION RESULTS[/]", style="blink cyan on grey15")
    table = Table(box=None)
    for stat, result in data.items():
        table.add_row(f"[dim]{stat}[/]", f"[blue] {result}[/]")
    table.add_row("[cyan]Mutation Score:[/]", f"[dim yellow2]{mutation_score:.1f}[/]")
    console.print(table, justify="center")

    return mutation_score


if __name__ == "__main__":
    if update_mutation_score() < MINIMUM_MUTATION_SCORE:
        raise SystemExit(1)
