# Ingest: pulling the month inside a Claude Code session

Ingest is the only phase that talks to a source system, and it runs inside
a Claude Code session because that is where the NetSuite and Supermetrics
MCP connectors exist. Cloudflare Pages cannot reach NetSuite, so the build
(`python -m src.build`) never does: it reads what ingest committed.

Python cannot call an MCP tool; Claude can. So every step below has the same
shape: **the CLI prints exactly what to run, Claude runs the tool and saves
the rows to a file, the CLI turns the file into a snapshot.** The adapter
code that shapes and validates the rows is identical whether the rows came
from a file or from a programmatic executor, and it is what `tests/test_ingest.py`
proves with fakes.

Everything ingest writes goes through `src.freeze.SnapshotStore.write_open`,
which refuses to overwrite a frozen snapshot. That refusal is the freeze rule
made mechanical; do not work around it.

## Before you start

- You are on the refresh branch: `git checkout -b refresh/YYYY-MM` (the
  reporting month, i.e. the month that just closed).
- The session has both connectors: `mcp__NetSuite__*` and
  `mcp__Supermetrics_Marketing_Analytics__*` appear in the tool list.
- `python3 -m pytest tests -q -p no:cacheprovider --ignore=tests/test_mutation.py`
  is green before you touch data.
- Decide the as-of date once and use it everywhere: `AS_OF=2026-09-05`.
  Revenue-to-date pulls include transactions through that day, and the
  build prints it in the footer.

Save tool output to files under a scratch folder, never into the repo.
Row files are JSON in any of these shapes: a list of row objects;
`{"items": [...]}` (what the NetSuite tool returns); `{"rows": [...]}`;
`{"data": [...]}` where data is a list of objects or a list of lists whose
first row is the header (the Supermetrics results shape). Numbers must stay
as the tool gave them (strings or JSON numbers) — never re-typed as floats
by hand.

## 1. Which months need pulling

```
python -m src.ingest plan marketing_spend --as-of $AS_OF
python -m src.ingest plan cohorts_m1      --as-of $AS_OF
python -m src.ingest plan cohorts_m13     --as-of $AS_OF
```

`plan` lists months that are OPEN or CLOSED-but-not-promoted. Frozen months
are never listed (`src.ingest.queries.months_to_pull`). For cohorts you
will additionally re-pull the frozen months — see step 3 — because their
live repeat revenue grows even though their M1 is held.

## 2. Marketing spend (NetSuite)

For each month `plan` listed:

```
python -m src.ingest sql marketing_spend 2026-08
```

prints one SuiteQL statement and its `query_hash`. Run it with

    mcp__NetSuite__ns_runCustomSuiteQL   { "query": "<the printed SQL>" }

save the result to `spend_2026-08.json`, then

```
python -m src.ingest write marketing_spend 2026-08 --rows spend_2026-08.json
```

The adapter asserts every account is `66212.*` / `66215.*` and not `96212.*`
(the GarageExperts NAF mirrors our chart of accounts almost line for line)
and records `BUILTIN.DF` account names so unbudgeted accounts can be
labelled by name on the page.

Prior-year months work the same way (`... marketing_spend 2025-01`). The
executive page's year-over-year spend tiles need every month of the prior
year's comparison window; the build names exactly which are missing.

## 3. Cohorts: M1 and revenue to date (NetSuite)

```
python -m src.ingest sql cohorts_m1 2026-08 --as-of $AS_OF
```

prints TWO statements, in order: `[1/2]` month-one revenue
(`src/ingest/queries/cohorts_m1.sql`) and `[2/2]` revenue to date
(`src/ingest/queries/cohorts_revenue_to_date.sql`). Run both, save each to
its own file, and pass them in that order:

```
python -m src.ingest write cohorts_m1 2026-08 --rows m1_2026-08.json rtd_2026-08.json --as-of $AS_OF
```

- An OPEN or CLOSED month is written to `cohorts_m1.json`.
- A FROZEN month is written to `cohorts_m1_live.json` beside it — the
  sidecar. It carries `repeat_revenue_live` (the build adds it to the frozen
  M1: "frozen M1 + live repeat") and `live_at_last_pull` (which the build
  compares against the frozen figure for drift). The frozen file is not
  touched. **Never promote a `_live` sidecar.**
- If revenue to date comes back below M1 the adapter refuses: that is
  impossible unless credits were applied outside the M1 window. Investigate.

Do this for every cohort month the dashboards show: the rolling twelve
months, the year to date, and the same months last year. One cohort per
call — transaction joins over a multi-month range hit the 180 s timeout.

First-90-days cohorts (`cohorts_m13`) are written only once the window has
closed (month end + 90 days ≤ as-of); the adapter refuses earlier.

```
python -m src.ingest sql   cohorts_m13 2026-05
python -m src.ingest write cohorts_m13 2026-05 --rows m13_2026-05.json --as-of $AS_OF
```

## 4. Leads: quality and routing (NetSuite)

Both are customer-table-only and cover the trailing thirteen months in one
call; `<month>` is the END of the range.

```
python -m src.ingest sql   lead_quality 2026-08
python -m src.ingest write lead_quality 2026-08 --rows lq.json
python -m src.ingest sql   lead_routing 2026-08
python -m src.ingest write lead_routing 2026-08 --rows lr.json [--rep-names names.json]
```

Frozen lead months are left alone; the rest are (re)written. If a rep id
outside `{8766 Alexis, 5803 Dan, 16226 Parker}` appears, run the companion
lookup at the foot of `lead_routing.sql` and pass `--rep-names` with
`{"<id>": "<BUILTIN.DF name>"}` so the "other" bucket lists a person, not
a number.

## 5. Social and ads (Supermetrics)

```
python -m src.ingest spec linkedin 2026-08
```

prints the `data_query` arguments for each query the domain needs
(LinkedIn needs two: page statistics with a date breakdown, then share
statistics with none — they are different populations and are never mixed).
For each printed spec:

    mcp__Supermetrics_Marketing_Analytics__data_query               <printed arguments>
    mcp__Supermetrics_Marketing_Analytics__get_async_query_results  { "schedule_id": "<from the first call>" }  (poll until ready)

Save the results to files, in the same order, and:

```
python -m src.ingest write linkedin  2026-08 --rows li_page.json li_share.json --as-of $AS_OF [--range 2026-08-01 2026-08-31]
python -m src.ingest spec  instagram 2026-08   /  write instagram 2026-08 --rows ig.json --as-of $AS_OF
python -m src.ingest spec  meta_ads  2026-08   /  write meta_ads  2026-08 --rows fa.json --as-of $AS_OF
```

`--range` is the range the result actually covered; omit it if the saved
file carries `meta.start_date` / `meta.end_date`.

Accounts (facts, not settings): LinkedIn `LIP` 6735901 · Instagram `IGI`
17841402384139665 · Meta Ads `FA` act_1162719948574137.

**The coverage guard.** `write` REFUSES a month whose result does not cover
it entirely: the month is not over as of `--as-of`, the result range is not
exactly the first to the last day, or the dated rows do not reach both
ends. v1 published five partial LinkedIn months as final and understated
impressions by about 2×. Pull again later; do not argue with the refusal.

**Field guards** (also refusals): LinkedIn page metrics and
`total_share_impressions` in one query; share impressions with a date
dimension; Meta `offsite_conversions_fb_pixel_lead` (entirely NULL for this
account — leads are `onsite_conversion.lead_grouped`); a Meta lead metric
without `campaignobjective`. Leads and cost per lead are computed over
`OUTCOME_LEADS` campaigns only; `OUTCOME_SALES` / `OUTCOME_TRAFFIC` rows
carry `leads: null`.

If the API rejects a field name, run
`mcp__Supermetrics_Marketing_Analytics__field_discovery` for the source and
correct the constant in `supermetrics.py`. Do not substitute a
different-looking metric.

## 6. Manual files (GMB, Hotjar)

Save the month's export as `data/manual/<year>/gmb_<YYYY-MM>.json` (or
`.csv`; `hotjar_…` likewise). Required fields: GMB `impressions`,
`website_clicks`, `calls`, `direction_requests`; Hotjar `recordings`,
`rage_click_recordings`, `feedback_responses`. Then

```
python -m src.ingest manual gmb    2026-08
python -m src.ingest manual hotjar 2026-08
```

A missing file prints `pending: …` and the build renders a "data pending"
callout for that section. A present file with a field missing, a negative
count, or a `period` naming another month is refused.

## 7. Build, review, then promote

Run the build (`python -m src.build --as-of $AS_OF`), read
`reports/change_log_<month>.md` and the console drift report, and only
then freeze the months that are closed and reviewed:

```
python -m src.freeze promote 2026-08 marketing_spend --by "Jules" --note "Published in the Sep 2026 refresh"
python -m src.freeze promote 2026-08 cohorts_m1      --by "Jules" --note "Published in the Sep 2026 refresh"
python -m src.freeze promote 2026-05 cohorts_m13     --by "Jules" --note "90-day window closed 2026-08-29"
python -m src.freeze status cohorts_m1
```

`promote` refuses a month that is not calendar-closed, and for `cohorts_m13`
a window that has not closed. Commit each promotion on its own; the commit
message is the audit trail. Never promote `lead_*` months you expect to
keep converting, and never promote a `_live` sidecar.

## Constraints that shaped this (SuiteQL via the MCP tool)

| Constraint | Consequence here |
|---|---|
| 180-second timeout | One cohort or one calendar month per transaction-join query. Customer-table-only queries may span the range. |
| 5,000-row cap, silent | Every query aggregates in SQL; `run()` refuses a result that reaches the cap. |
| No CTEs | No `WITH`. Sub-selects are fine. |
| No bind parameters | `:name` placeholders are substituted in Python with a strict allowlist; dates are validated `YYYY-MM-DD` and emitted as `TO_DATE('…','YYYY-MM-DD')`. |
| `BUILTIN.DF` | Display names for ids; grouped by id, looked up separately. |

## Refusals you will meet, and what they mean

| Message | Meaning | Do |
|---|---|---|
| `… is FROZEN. A live pull may not overwrite it.` | You wrote directly to a frozen month. | Use `ingest_*` / the CLI, which routes frozen cohorts to the `_live` sidecar; if the figure must change, `python -m src.freeze amend … --reason`. |
| `rows span … not …; the source has not finished the month` | Partial month from Supermetrics. | Wait; pull again. |
| `… is not over as of …` | You asked for the current month. | Ingest only closed months. |
| `revenue to date … below M1 … impossible` | Credits applied outside the M1 window. | Investigate in NetSuite before writing anything. |
| `parameters […] are not referenced by the SQL` | A misspelt parameter name. | Fix the name; a literal `:name` would otherwise reach SuiteQL. |
| `… reaches the SuiteQL cap` | Truncated result. | Narrow the range or aggregate further. |
| `pending: GMB figures … have not been supplied` | Manual file missing. | Add the file, or ship with the pending callout. |
