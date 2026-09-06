# Narrative content

Numbers are computed; the story is written here, once a month, and reviewed.
Prose never contains a literal number. Every figure is a reference the data
layer resolves, so a removed or renamed metric fails the build loudly instead
of leaving stale digits behind.

## Files

    content/YYYY-MM/executive.md
    content/YYYY-MM/marketing-ops.md
    content/YYYY-MM/sales.md

One file per dashboard per reporting month. Edit last month's wording without
touching code; diff this month's story against last month's with `git diff`.
Start a new month by copying the previous month's three files, changing
`period:` in the front-matter, and rewriting the prose.

A missing file is not a build failure: the page renders with a "Narrative
pending" callout, so numbers can be refreshed before the story is written.
A malformed file, or one that references a metric the build does not have,
fails the page.

## Syntax

Markdown with YAML front-matter, rendered through the same Jinja2 environment
as the templates (`StrictUndefined`, so a typo in an id is a build failure),
then converted to HTML by Python-Markdown.

- `{{ m("aug26.new_customers") }}` — a registered metric. Renders as a
  `<span data-metric=…>` carrying its kind and period label.
- `{{ d("aug26.new_customers", "jul26.new_customers") }}` — a relative delta
  between two metrics via the single `delta()`; never a point difference.
- `{{ c("legacy_multiple") }}` — a claim declared in front-matter (below), or
  one the build registers itself (`build.drift_story`, `budget26.vs_plan_story`,
  `fy26.on_track`).

Metric ids are `<period>.<measure>`. Periods: `aug26` (a month), `ytd26`
(Jan–reporting month), `ytd25` (the same months last year), `fy26`/`fy25`
(full years), `r12` (twelve calendar-closed months ending with the reporting
month), `r14`/`r3` (routing and pipeline windows), plus subject prefixes
`budget26`, `corr26`, `ask26`, `retention`, `vintage`, `truad`, `recon`,
`m13`, `geo`, `build`. The full list for a build is whatever
`src/populate.py` registers; a wrong id fails with the nearest matches named.

## Claims

A claim is a derived statement with an assertion. If the assertion fails, the
build fails and names the claim — a sentence that stopped being true cannot
stay on the page because nobody re-read it.

```yaml
claims:
  legacy_multiple:
    expr: "vintage.pre2018_avg_annual_net / vintage.band_2025_avg_annual_net"
    assert: "between(9, 13)"
    render: "{:.0f}×"
  shortfall:
    expr: "fy26.shortfall_after_available"
    assert: "nonzero"
    render:
      positive: "leaves roughly ${:,.0f} still to find"
      negative: "fits inside the plan with roughly ${:,.0f} to spare"
```

- `expr` is arithmetic over metric ids: `+ - * /`, unary minus, numeric
  constants, and the functions `delta(a, b)`, `abs`, `min`, `max`. Nothing
  else parses. There is no `eval()`.
- `assert` is one of `positive`, `negative`, `nonzero`, `between(a, b)`,
  `at_least(x)`, `at_most(x)`.
- `render` is a Python format string applied to the value, or a mapping of
  `positive` / `negative` / optional `zero` format strings chosen by the sign
  and applied to the absolute value. Use the mapping wherever the wording
  itself depends on the sign ("under plan" / "over plan") so the sentence
  cannot contradict the number.

## Sections and placement

Each `##` heading is a section, keyed by its slug (`Are we growing?` →
`are-we-growing`). The page template places each section where it belongs
(`{{ narrative.section('are-we-growing') }}`), so tiles and charts keep their
layout and the prose sits beneath them. A section the template has no slot
for fails the build: prose that was written is shown or deliberately removed,
never lost. The slots each template offers are listed in its header comment.
The `#` title line is dropped (the page shell carries the title).

**Headlines that expand.** A template may place a section with
`narrative.brief(slug)` instead of `section(slug)`. The executive page does
this for "The three things to take from this month": each `###` heading
becomes a closed line the reader can open, and the paragraphs under it are
the evidence. Write those `###` headings as complete sentences that stand on
their own ("August rebounded; July was a dip, not a trend"), because for
most of the room the heading is all that will be read. Prose before the first
`###` stays visible as the lead. Figures inside the body keep their
`data-metric` spans, so a collapsed paragraph is exactly as traceable as an
open one.

## Not carried forward

```yaml
not_carried_forward:
  - "v1 Budget: '$33,177 under YTD'. The approved budget is $206,346; corrected below."
```

Nothing from a prior build is dropped silently. Retired findings are listed
with the reason and rendered at the foot of the page under "Not carried
forward from the previous build". They are marked `data-retired`, which is the
one place the validator allows a typed number: a quotation of a retired
finding is not a statement about this month. Forbidden words (`points`,
`gross`) are still refused there.

## Rules for drafted narrative (from the brief)

- Never state a number the data layer cannot produce.
- Measured is measured; inference reads as inference ("likely", "consistent with").
- Volatility is not a trend. One outlier month is an outlier.
- Lead with the least flattering material finding.
- Competitors are Competitor A / B / C, never named.
- Every recommendation carries a price and a success measure.
- No emoji beyond the four flag icons. Beginner reading level; expand
  abbreviations on first use. "Installer" is a role, not a skill level.
- Named people and accounts may appear on Marketing Ops and Sales, never on
  Executive.
- Month names in prose are allowed but produce a gate warning so a
  left-over comparison is read by a person before it is read by leadership.
