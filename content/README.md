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

## Syntax

Markdown with YAML front-matter, rendered through the same Jinja2 environment
as the templates (`StrictUndefined`, so a typo in an id is a build failure).

- `{{ m("aug26.new_customers") }}` — a registered metric. Renders as a
  `<span data-metric=…>` carrying its kind and period label.
- `{{ d("aug26.new_customers", "jul26.new_customers") }}` — a relative delta
  between two metrics via the single `delta()`; never a point difference.
- `{{ c("legacy_multiple") }}` — a claim declared in front-matter: a derived
  statement with an assertion. If the assertion fails, the build fails and
  names the claim.

Metric ids are `<period>.<metric>`. Periods: `aug26` (a month), `ytd26`
(Jan–reporting month), `jj26` (Jan–Jul, the frozen comparison window),
`fy25`, `r12` (twelve calendar-closed months ending with the reporting
month), `q3_26`. Metrics are snake_case.

## Rules for drafted narrative (from the brief)

- Never state a number the data layer cannot produce.
- Measured is measured; inference reads as inference ("likely", "consistent with").
- Volatility is not a trend. One outlier month is an outlier.
- Lead with the least flattering material finding.
- Competitors are Competitor A / B / C, never named.
- Every recommendation carries a price and a success measure.
- No emoji beyond the four flag icons. Beginner reading level; expand
  abbreviations on first use. "Installer" is a role, not a skill level.
- Nothing from a prior build is dropped silently. If a finding no longer fits,
  it is listed under "Not carried forward" with the reason.
