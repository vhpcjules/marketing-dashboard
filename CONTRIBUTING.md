# Contributing

Small repo, strict rules. The rules exist because each one was learned the
expensive way in v1; METHODOLOGY.md has the reasoning, this file has the
procedure.

## The one rule

**Never commit a number that is not produced by the data layer.** No figure
is typed into a template, a narrative, or a test fixture by hand. If a page
needs a number, `src/data/` produces it, `src/units` formats it, and the
template receives it. The validation gate scans for the alternative.

## Branches and releases

- One branch per monthly refresh: `refresh/YYYY-MM` (the reporting month,
  not the month you are doing the work).
- Open a pull request. CI runs the full suite, including
  `tests/test_mutation.py`, then the build and the validation gate. Nothing
  deploys from a PR.
- Merge to `main` when Jules approves. Cloudflare Pages builds `main` with
  `python3 -m src.build && python3 -m src.validate.gate dist`; a failing gate
  fails the deploy and the previous deployment stays live.
- Tag each published refresh `vYYYY.MM` once it is live. The tag is what a
  restatement refers back to.

## The freeze rule

A number that has been presented to leadership does not silently change:
closed periods are read from their frozen snapshot in `data/snapshots/`, and
only months never previously published use live figures. Drift found by the
build is logged to `reports/` and never applied by the build; accepting a
restatement is a human act (`amend`) in its own commit with a reason.

## data/ and content/ are append-mostly

`data/snapshots/`, `data/manual/`, `reports/` and `content/` are the
historical record and are **not** gitignored. Normal refreshes add files. An
amendment to an existing file gets its own commit whose message says what
changed, by how much, and why - the git log is the audit trail, and a commit
that says "update snapshot" is a failure of the process.

## public/ and redirects

The build copies `public/` into `dist/` verbatim. `public/_redirects` holds
the Cloudflare Pages rules that keep the six v1 dashboard URLs working with
permanent (301) redirects to the v2 pages. `tests/test_repo_hygiene.py`
checks all six are present; do not remove one.

## Secrets

There are none in this repo, and pre-commit runs gitleaks to keep it that
way (`pip install pre-commit && pre-commit install`). Tokens for NetSuite,
Supermetrics and HubSpot live in environment variables locally and in
Cloudflare's secret store for the build. MCP configuration containing tokens
stays out of git. `.gitleaks.toml` allowlists the long numeric ids that are
not secrets (Supermetrics account ids, the NetSuite subsidiary id, GL account
numbers) so the scanner stays quiet enough to be trusted.

## Style

- Every number is `Decimal`. Never `float`.
- Every formatted figure goes through `src.units`. `src.units.delta` is the
  only delta function; the gate asserts this by AST scan.
- A percentage-point difference is never displayed. State the range instead.
- Module docstrings explain why. Comments mark rules that were learned the
  hard way. Dataclasses, frozen where sensible.
- Run before you push:
  `python3 -m pytest tests -q -p no:cacheprovider`
