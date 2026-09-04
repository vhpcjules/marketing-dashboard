"""Mutation tests: prove the suite has teeth.

A test asserting "we have a check for X" proves nothing. The only honest
proof that a detector works is to break the code deliberately and confirm
the suite goes red.

Each case below copies the repo to a temp directory, applies one textual
mutation that reintroduces a real v1 bug, runs the test suite there, and
asserts it FAILS. If a mutation survives, the guard protecting it is
decorative and this file fails instead.

This is the answer to the question "how would you prove the validation layer
catches the point/percent bug rather than merely asserting it does".
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Mutation:
    id: str
    bug: str            # the v1 defect this reintroduces
    file: str
    find: str
    replace: str
    expect_in_output: str


MUTATIONS = [
    Mutation(
        id="delta_returns_point_difference",
        bug="v1: phone capture 45.6%->55.7% rendered as '+10.1%' (three copies)",
        file="src/units.py",
        find="    return (cur - prev) / prev * Decimal(100)",
        replace="    return cur - prev",
        expect_in_output="TestDeltaAgainstV1BugValues",
    ),
    Mutation(
        id="pctpoints_renders_happily",
        bug="v1: a point difference reached a template wearing a percent sign",
        file="src/units.py",
        find="    def __format__(self, spec: str) -> str:\n        raise self._refuse()",
        replace="    def __format__(self, spec: str) -> str:\n        return f'{self.value}'",
        expect_in_output="TestPctPointsCannotRender",
    ),
    Mutation(
        id="colour_ignores_direction",
        bug="v1: average deal fell 63% and was styled green",
        file="src/units.py",
        find='    good = (change > 0) if higher_is_better else (change < 0)',
        replace='    good = True',
        expect_in_output="TestDirectionAndColourAgree",
    ),
    Mutation(
        id="money_period_label_optional",
        bug="v1: unlabelled currency figures; M1 and lifetime figures conflated",
        file="src/units.py",
        find='        if not self.period or not str(self.period).strip():\n            raise ValueError(\n                "Money requires a period label',
        replace='        if False:\n            raise ValueError(\n                "Money requires a period label',
        expect_in_output="TestMoneyRequiresPeriod",
    ),
    Mutation(
        id="credits_blended_into_monthly_spend",
        bug="August 2026 would publish as MINUS $9,493 of marketing spend",
        file="src/data/spend.py",
        find="        elif basis is Basis.TRUE_OPERATING:",
        replace="        elif basis is Basis.TRUE_OPERATING and False:",
        expect_in_output="TestAugustIsNotNegative",
    ),
    Mutation(
        id="cancellation_ignored",
        bug="cancelled World of Concrete budget would still read as headroom",
        file="src/data/spend.py",
        find="        if honour_cancellations:\n            for c in self.budget.get(\"cancellations\", []):",
        replace="        if False:\n            for c in self.budget.get(\"cancellations\", []):",
        expect_in_output="TestWorldOfConcreteCancellation",
    ),
    Mutation(
        id="budget_vs_actual_drops_unbudgeted_rows",
        bug="v1: budget table listed 6 of 10 rows, under-reporting actual by $41,777",
        file="src/data/spend.py",
        find="        accounts = sorted(set(self.budget[\"accounts\"]) | set(actual))",
        replace="        accounts = sorted(set(self.budget[\"accounts\"]))",
        expect_in_output="test_every_dollar_of_actual_is_attributed",
    ),
    Mutation(
        id="agency_surcharge_dropped",
        bug="v1 priced the 90-day retargeting run at $3,000; all-in it is $3,600",
        file="src/data/spend.py",
        find='    rate = Decimal("0.20")  # kept in step with the formula string below',
        replace='    rate = Decimal("0")  # kept in step with the formula string below',
        expect_in_output="TestAgencySurcharge",
    ),
    Mutation(
        id="naf_included_in_marketing_spend",
        bug="96212.* is the GarageExperts franchisee fund, not VHPC spend",
        file="src/data/spend.py",
        find='GL_EXCLUDE_PREFIXES = ("96212",)',
        replace="GL_EXCLUDE_PREFIXES = ()",
        expect_in_output="test_naf_prefix_is_declared_in_config",
    ),
]


def _run_suite(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_units.py", "tests/test_spend.py",
         "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=root, capture_output=True, text=True, timeout=300,
    )


@pytest.fixture(scope="module")
def clean_tree(tmp_path_factory) -> Path:
    """A pristine copy, to confirm the suite passes before we break anything."""
    dst = tmp_path_factory.mktemp("clean")
    _copy_repo(dst)
    return dst


def _copy_repo(dst: Path) -> None:
    for item in ("src", "tests", "data"):
        shutil.copytree(REPO / item, dst / item,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def test_baseline_suite_passes(clean_tree):
    """Guard against a false positive: the mutations below must be the reason."""
    r = _run_suite(clean_tree)
    assert r.returncode == 0, f"baseline suite is not green:\n{r.stdout}\n{r.stderr}"


@pytest.mark.parametrize("m", MUTATIONS, ids=lambda m: m.id)
def test_mutation_is_caught(m: Mutation, tmp_path):
    _copy_repo(tmp_path)
    target = tmp_path / m.file
    src = target.read_text()

    assert m.find in src, (
        f"mutation {m.id!r} no longer applies - the code it targets has changed.\n"
        f"Update the mutation so this guard stays proven, do not delete it."
    )
    target.write_text(src.replace(m.find, m.replace, 1))

    r = _run_suite(tmp_path)
    out = r.stdout + r.stderr

    assert r.returncode != 0, (
        f"MUTATION SURVIVED: {m.id}\n"
        f"Reintroduced bug: {m.bug}\n"
        f"The suite stayed green, so nothing is actually guarding this.\n{out}"
    )
    assert m.expect_in_output in out, (
        f"mutation {m.id} was caught, but not by the test expected to catch it "
        f"({m.expect_in_output}). Verify the right guard is doing the work.\n{out}"
    )
