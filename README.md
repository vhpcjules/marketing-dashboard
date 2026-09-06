# Versatile marketing dashboards — the monthly refresh runbook

Three internal dashboards (Executive, Marketing Ops, Sales) built from
committed data, validated before they render, and served behind Cloudflare
Access. Every number is NET, Decimal, traceable to a snapshot, and — once
shown to leadership — frozen.

This file is the procedure. The reasoning lives elsewhere and is worth
reading once:

- `METHODOLOGY.md` — what every number means and the rules it obeys.
- `CONTRIBUTING.md` — branches, releases, the freeze rule, style.
- `BRAND.md` — what the pages look like and why.
- `src/ingest/README.md` — the step-by-step ingest procedure with the exact
  MCP tool names and SQL files.

## How it works in one paragraph

There are two phases. **Ingest** runs inside a Claude Code session, where
the NetSuite and Supermetrics MCP connectors exist; it pulls each month's
figures, validates them, and writes them as JSON snapshots under
`data/snapshots/<YYYY-MM>/`. **Build** is a pure function of the committed
repository — no network — so it produces the same pages on a laptop, in
GitHub Actions and on Cloudflare Pages. The split is a network fact, not a
preference: Cloudflare cannot reach NetSuite. Between the two sits the
freeze rule: a period that has been published is *promoted*, after which no
live pull can overwrite it, and every later build compares the live value
against the frozen one and reports the drift instead of applying it.

## Prerequisites

- Python 3.11. `python3 -m pip install -r requirements.txt` (Jinja2 and
  pytest; nothing else, on purpose — every dependency is a future break).
- A Claude Code session with the **NetSuite** and **Supermetrics** MCP
  connectors, for the ingest phase only.
- Write access to the repository and the ability to open a pull request.
- The approved budget for the year in `data/manual/<year>/approved_marketing_budget.json`
  (budget is not in NetSuite; report −197 returns zero for every line).
- The suite green before you start:
  `python3 -m pytest tests -q -p no:cacheprovider --ignore=tests/test_mutation.py`

## The monthly loop

Do this in the first week of the month, for the month that just closed.
Below, `2026-08` is the reporting month and `2026-09-05` the as-of date.

1. **Branch.** `git checkout main && git pull && git checkout -b refresh/2026-08`

2. **Ingest, in the session.** Follow `src/ingest/README.md` end to end:
   `plan` → `sql`/`spec` → run the MCP tool → save rows → `write`, for
   marketing spend, cohorts (M1 and revenue to date, including the frozen
   months so their live repeat revenue and drift values refresh),
   first-90-days cohorts whose window has closed, lead quality and routing,
   LinkedIn / Instagram / Meta Ads, and the GMB and Hotjar files. Commit
   the new and changed snapshots: one commit per domain is plenty.

3. **Promote the periods that are now closed.** Only after step 2 has been
   reviewed, and only what the build will publish:
   ```
   python -m src.freeze promote 2026-08 marketing_spend --by "Jules" --note "Published in the Sep 2026 refresh"
   python -m src.freeze promote 2026-08 cohorts_m1      --by "Jules" --note "Published in the Sep 2026 refresh"
   ```
   `cohorts_m13` promotes only once the cohort's 90-day window has closed
   (`promote` refuses otherwise). Commit each promotion on its own.

4. **Build.** `python -m src.build --as-of 2026-09-05`
   Writes `dist/`, runs the validation gate, writes
   `reports/change_log_2026-08.md`, and prints a summary. Exit code 1 means
   *do not deploy*; the summary says why (a skipped page, a new drift
   breach, a failed gate).

5. **Review the change log.** `reports/change_log_2026-08.md` lists every
   tracked metric: prior month, this month, relative change, direction, and
   whether the move exceeds its variance threshold (two standard deviations
   of the trailing twelve month-over-month changes, or a configured
   per-metric value). Anything flagged **YES** gets a sentence in the
   narrative or a reason it is noise. Read the drift report too.

6. **Draft the narrative.** Edit `content/2026-08/*.md` per
   `content/README.md`. Prose never contains a literal number; figures are
   `{{ m("…") }}` references the build resolves, and each `##` section is
   placed by a named slot in the page template. Until the month's files
   exist the pages build with a "Narrative pending" callout, so numbers can
   go out for review before the story is written.

7. **Build again.** Same command. The gate re-checks the rendered pages,
   including the narrative. Fix anything red; never `--skip-gate` for a
   deploy.

8. **Pull request.** Push `refresh/2026-08`, open a PR. CI runs the full
   suite (including the mutation suite), then the build and the gate.
   Nothing deploys from a PR.

9. **Merge** when Jules approves. Cloudflare Pages builds the production
   branch (`main`; set it in the Pages project if the repo does not have one
   yet) with
   `python3 -m src.build && python3 -m src.validate.gate dist`; a failing
   gate fails the deploy and the previous deployment stays live.

10. **Tag.** `git tag v2026.08 && git push --tags` once the deploy is live.
    The tag is what a later restatement refers back to.

## Cloudflare Pages settings

Two ways to publish; either is enough.

**Git integration.** The Pages project `marketing-ca5` was created against the
v1 repository. This repository began on 2026-09-04, so until the project is
reconnected to `vhpcjules/marketing-dashboard` (Pages project -> Settings ->
Builds & deployments -> Source) no push here reaches Cloudflare. Reconnecting
keeps the custom domain and the Access policy.

**Direct upload from GitHub Actions.** `.github/workflows/deploy.yml` builds,
gates and uploads `dist` to the same project on every push to `main`. It needs
two repository secrets: `CLOUDFLARE_API_TOKEN` (a token with "Cloudflare
Pages: Edit") and `CLOUDFLARE_ACCOUNT_ID`. Without them the job builds and
gates but skips the upload with a notice.

Settings for the Git integration, if used:

- Production branch: `main`. Build command: `python3 -m src.build && python3 -m src.validate.gate dist`.
  Output directory: `dist`. Both commands default to today's date, so no
  arguments are needed; the reporting month is the month before the build.
- Environment variable `PYTHON_VERSION` = `3.11`. Pages installs
  `requirements.txt` (Jinja2, Markdown, PyYAML, pytest) before the build.
- Cloudflare Access sits in front of the deployment; the `_headers` file
  adds `noindex` as a second line of defence.

## Troubleshooting

**Drift breach.** The build prints `DRIFT DETECTED … <<< BREACH` and writes
`reports/restatement_<as_of>.md`. A frozen figure now reads differently in
the ledger (late credit memos are the usual cause). Nothing was changed. If
the breach is already recorded in the frozen file's `live_at_last_pull`
(the value known when the figure was held), the build continues and the
Executive page carries an amber flag. If it is **new**, the build stops.
Decide, as a person: *hold* the published figure (record the new live value
with `python -m src.freeze amend … --reason "…"`, body unchanged except
`live_at_last_pull`) or *accept* the restatement (`amend` with the new
figure). Either way, one commit, with the reason in the message.

**Frozen-overwrite refusal.** `… is FROZEN. A live pull may not overwrite
it.` You tried to write a published month. Use the ingest CLI, which routes
frozen cohorts to the `cohorts_m1_live` sidecar; the frozen file is meant
to stay exactly as it is.

**Partial-month refusal.** `rows span … the source has not finished the
month` or `… is not over as of …` from a Supermetrics write. The month is
not complete at the source. Wait and pull again. This is the mechanical
form of "never treat a partial month as final", which cost five months of
2× understated LinkedIn impressions in v1.

**Missing manual file.** `pending: GMB figures for August 2026 have not
been supplied yet…` The build does not fail; the section renders a "data
pending" callout and the Executive page carries an amber flag. Add
`data/manual/2026/gmb_2026-08.json` (fields in `src/ingest/manual.py`) and
rebuild, or ship with the callout.

**Page skipped.** `SKIPPING executive: … contract IDs not registered: […]`.
The data on disk cannot produce a figure the page asks for — most often a
prior-year month that was never ingested. The message names the IDs and
the months. Ingest them; the build never fills a gap with a guess.

**Gate failure.** `VALIDATION GATE FAILED` with a table of findings, also
in `reports/gate_<month>.md`. Each check is a v1 bug: untraced digits in
prose, a delta styled the wrong colour, a total that does not sum, a
percentage-point difference, an external script. Fix the cause, not the
check.

**Tests red after a snapshot edit.** `tests/test_freeze.py::TestRepoSnapshots`
and `tests/test_spend.py` assert the committed figures. If you meant to
change a frozen figure, you amended it — update the assertion in the same
commit with the reason. If you did not mean to, revert.

## What each directory is

- **`src/`** — the code. `units.py` (Decimal unit types and the one `delta`),
  `periods.py` (which months are closed, computed from a date),
  `freeze.py` (snapshots, promotion, drift), `data/` (spend, cohorts,
  targets, and the canonical SQL under `data/queries/`), `ingest/`
  (source adapters and the session CLI), `render/` (the metric registry,
  templates environment, chart specs, brand tokens), `validate/` (the
  gate), `build.py` (the orchestrator).
- **`data/snapshots/<YYYY-MM>/`** — one JSON file per period per domain,
  each with a `_meta` block (pulled_at, query_hash, row_count, frozen).
  The historical record; never gitignored. `*_live.json` files are the
  never-frozen sidecars for months whose main file is frozen.
- **`data/manual/<year>/`** — analyst inputs: the approved budget,
  corrections that cannot be read off the ledger, TRUAD media spend, and
  the GMB / Hotjar monthly exports.
- **`content/<YYYY-MM>/`** — the narrative, one Markdown file per
  dashboard per month, with figures as registry references.
- **`src/populate.py`** — every figure a page shows, registered once per
  page from the loaded inputs; `src/render/contracts.py` declares what each
  template will ask for, with period placeholders resolved per month.
- **`templates/`** — Jinja2 pages and components. Strict: an unknown
  variable or metric ID fails the build. Templates address the reporting
  month through `ids.cur`, never a month name.
- **`assets/`** — brand CSS, self-hosted Inter, vendored Chart.js. No CDN.
- **`public/`** — copied into `dist/` verbatim; holds the Cloudflare
  `_redirects` that keep the six v1 bookmarks working.
- **`reports/`** — restatement logs, change logs, gate reports. Tracked.
- **`reference/v1/`** — the previous dashboards, kept as the corpus the
  validation checks are proven against.
- **`tests/`** — the suite. `test_mutation.py` breaks the code on purpose
  and proves the guards catch it.
- **`dist/`** — build output. Gitignored; Cloudflare builds it from source.
