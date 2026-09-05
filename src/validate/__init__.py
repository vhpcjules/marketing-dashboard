"""Validation layer: the build fails on any violation.

One module per check family; gate.run_gate runs them all.

    numbers      totals add up, metrics agree, no unresolved values, SQL text
    language     forbidden terms, period labels, delta colour, %-labelled points
    narrative    orphaned typed numbers, stale month names
    structural   tag balance, canvas bindings, delta helpers, chart clipping
    code         AST scan: exactly one delta() in src/

See tests/test_v1_corpus.py for what each check finds in the v1 dashboards.
"""

from .findings import Finding

__all__ = ["run_gate", "GateReport", "Finding"]


def __getattr__(name: str):
    # Lazy so that `python -m src.validate.gate` does not import gate twice
    # (once as a package attribute, once as __main__) and warn about it.
    if name in ("run_gate", "GateReport"):
        from . import gate
        return getattr(gate, name)
    raise AttributeError(name)
