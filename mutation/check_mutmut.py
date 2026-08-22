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

"""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer
from rich import print as rprint
from rich.console import Console
from rich.table import Table

console = Console(force_terminal=True, color_system=None if os.environ.get("RALPH_LOOP") else "256")

MINIMUM_MUTATION_SCORE = 80.0

JsonDocument = dict[str, object] | list[object] | str | int | float | bool | None


def analyze_mutmut_report(file_path: str = "mutants/mutmut-cicd-stats.json") -> float:
    """Read the mutmut CI JSON results created each Sunday night.

    Arguments:
        file_path (str): Default filepath to read mutmut run stats from.

    Returns:
        mutation_score: float value, killed + timeout mutants as a% of all

    Raises:
        JSONDecodeError: If the report does not contain valid JSON.
        Exit: If the report file does not exist or the mutation score is below the minimum.
    """
    if not Path(file_path).exists():
        rprint(rf"[red]Error: Mutmut JSON report not found at [\]'{file_path}'")
        raise typer.Exit(code=1)
    data: dict[str, int] = {}
    with Path(file_path).open("r", encoding="utf-8") as fp:
        try:
            data = json.load(fp)
        except json.JSONDecodeError:
            rprint(rf"[red]JSONDecodeError [\]'{file_path}'")
            raise

    mutation_score: float = 0.0
    total_mutants = data.get("total", 0)
    skipped = data.get("skipped", 0)
    tested_mutants = total_mutants - skipped
    if tested_mutants > 0:
        killed = data.get("killed", 0)
        timeout = data.get("timeout", 0)
        mutation_score = ((killed + timeout) / tested_mutants) * 100

    table = Table(title="\n[cyan2]MUTMUT MUTATION RESULTS[/]\n", box=None, padding=(0, 2))
    for stat, result in data.items():
        table.add_row(f"[turquoise]  {stat}[/]", f"[blue] {result}[/]")
    table.add_row(f"[bold italic cyan2]  MUTATION SCORE: [/][bold italic yellow2]{mutation_score}[/]")
    console.print(table, justify="center")
    if mutation_score < MINIMUM_MUTATION_SCORE:
        raise typer.Exit(code=1)

    return mutation_score


if __name__ == "__main__":
    analyze_mutmut_report()
