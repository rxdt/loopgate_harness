"""Tests your tests. Mutmut breaks your source then runs test suite. If a test fails, the break was noticed,
a mutated source code variant is "killed." But if every test passes after mutation the break went unnoticed,
mutant code variant "survived". Mutmut exits 0 even with mutant survivors.

IMPORTANT: Mutmut only generates mutations on lines of code that are executed by tests.

Mutation testing mutates the source code, e.g. a boolean flips, a constant shifts, a comparison widens.
A mutant that dies proves a test assertion was working on that line. A mutant that SURVIVES is a variant of
source code that shows tests execute (i.e. test coverage is met there) BUT the test is flimsy:
has weak assertions, overly-mock, happy path only, missing edge cases, no real assert in test, weak logic.

Mutmut docs: https://mutmut.readthedocs.io/
    mutmut run       # generate mutants and run the test suite against all source code + its mutants
    mutmut browse    # TUI over the survivors
    mutmut results   # plain-text summary

Config in pyproject.toml [tool.mutmut]

Each mutant variant has a name, e.g. tests.test_file.x_lazy_assert__mutmut_2. Mutmut records mutant as killed
or survived before you inspect, and re-uses variant until source code changes.

CASE:
- best: 100% test coverage, run mutmut -> none survive (tests are sensitive to change)
- good: low coverage, run mutmut -> many mutations, none survive, you do nothing (gaps in coverage)
- worst: 100% coverage, run mutmut -> many mutations, ALL survive + no easy kills (many low quality tests)
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

from preferences.preferences import preferences_violations


def test_continue_two_for_loops_deep_is_flagged() -> None:
    """This test would go in tests/preferences/test_preferences.py but is here for demonstration purposes"""
    source = "for i in x:\n    for j in y:\n        continue\n"
    assert "Overly-nested" in preferences_violations("m.py", source)
