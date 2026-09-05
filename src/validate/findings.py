"""The one record type every check emits.

A check never prints, never raises on a violation, never returns a bool. It
returns Findings, and the gate decides what a Finding costs. That split is
what lets the corpus test in tests/test_v1_corpus.py ask "which bugs did you
find in v1?" instead of "did you crash on v1?".

Severity is two-valued on purpose. Either the build stops or it does not;
a third "notice" tier would be the tier nobody reads.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Finding", "FAIL", "WARN"]

FAIL = "fail"
WARN = "warn"


@dataclass(frozen=True)
class Finding:
    check: str          # e.g. "numbers.breakdown_table"
    file: str           # path relative to the dist dir, or "src/..." for code checks
    message: str        # one sentence a human can act on
    evidence: str = ""  # the offending text, trimmed
    severity: str = FAIL

    def __post_init__(self) -> None:
        if self.severity not in (FAIL, WARN):
            raise ValueError(f"severity must be {FAIL!r} or {WARN!r}, got {self.severity!r}")

    @property
    def is_failure(self) -> bool:
        return self.severity == FAIL

    def line(self) -> str:
        ev = f"  [{_trim(self.evidence)}]" if self.evidence else ""
        return f"{self.severity.upper():<4} {self.check:<34} {self.file}: {self.message}{ev}"


def _trim(s: str, n: int = 160) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"
