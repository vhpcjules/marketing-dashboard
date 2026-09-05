"""Ingest: the only phase that talks to a source system.

The pipeline has two phases, and the split is not a preference - it is a
network fact. Cloudflare Pages cannot reach NetSuite. So:

  INGEST  runs inside a Claude Code session, where the NetSuite and
          Supermetrics MCP connectors exist. It pulls, validates, and writes
          OPEN snapshots through src.freeze.SnapshotStore.write_open, which
          refuses to overwrite a frozen one. Nothing else in the repository
          may write under data/snapshots/.

  BUILD   (src/build.py) is a pure function of the committed repository. No
          network. It runs identically on a laptop, in CI, and on Cloudflare
          Pages, because everything it needs is already in git.

Each adapter takes a pluggable executor. In a session the executor is the
MCP tool (or a file of rows the tool returned); in tests it is a fake. The
adapters never import an MCP client - they cannot, there is none in Python -
so the boundary between "Claude called the tool" and "Python shaped the
result" is exactly the executor signature.

    queries.py       load SQL, strip comments, substitute parameters safely,
                     decide which months still need pulling
    netsuite.py      SuiteQL adapter (mcp__NetSuite__ns_runCustomSuiteQL)
    supermetrics.py  data_query -> get_async_query_results adapter, with the
                     LinkedIn / Meta field guards and the full-month coverage
                     refusal
    manual.py        file-based adapter for GMB and Hotjar; a missing file is
                     a MissingManualInput marker, never an exception
    common.py        Pull result type and the JSON carrier for Decimals

See src/ingest/README.md for the step-by-step session procedure.
"""

from .common import Pull, MissingManualInput  # noqa: F401  (re-exported for convenience)
