# Brand decisions for the dashboards

What the pages look like, and why each choice was made. The tokens live in
`assets/css/brand.css` (for the page) and `src/render/brand.py` (for
anything Python emits, chiefly chart colours); the tests assert the two
agree. This file is the reasoning.

One thing first: **Versatile High-Performance Coatings (VHPC) and Versatile
are the same company.** NetSuite says "VHPC LLC", the website says
"Versatile", the logo says both. Pages use the full name in the header and
"Versatile" in prose. Never write as though they were two entities.

---

## Palette

| Token | Name | Hex | RGB | CMYK (computed) | Role |
|---|---|---|---|---|---|
| `--vhpc-aqua` | Aqua Blue | `#014C8D` | 1, 76, 141 | 99, 46, 0, 45 | Primary ink: headings, tile values, table heads, chart bars, links |
| `--vhpc-deep` | Deep Blue | `#051732` | 5, 23, 50 | 90, 54, 0, 80 | Dark band (header, exec summary, internal banner); body text |
| `--vhpc-grey` | Grey | `#465564` | 70, 85, 100 | 30, 15, 0, 61 | Muted text: labels, explanations, axis ticks |
| `--vhpc-sandstone` | Sandstone | `#F8F8F8` | 248, 248, 248 | 0, 0, 0, 3 | Page ground; alternate table rows |
| `--vhpc-yellow` | Yellow | `#FBCF20` | 251, 207, 32 | 0, 18, 87, 2 | Highlight on dark surfaces; filled chip; the one emphasis bar |

CMYK values are the arithmetic conversion from RGB, for orientation only.
Print work uses the press values on the brand sheet, not these.

### Status colours (not brand)

| Token | Hex | Meaning |
|---|---|---|
| `--flag-red` | `#dc2626` | Urgent |
| `--flag-amber` | `#ca8a04` | Monitor |
| `--flag-green` | `#16a34a` | Working well |
| `--flag-blue` | `#0284c7` | A change we made; result still pending |

These are deliberately **off-palette**. They encode status, and a reader
must be able to tell "this is our blue" (Aqua, decoration) from "this is a
change whose result is pending" (flag blue, information) at a glance. If
the flags used brand colours, every Aqua heading would read as a signal.
The same four colours paint delta text (`.delta-good` green, `.delta-bad`
red) and status table rows, and nothing else.

## Why Aqua Blue replaces the v1 navy

v1 used `#1F4E79` everywhere. The brand primary is `#014C8D`. Their
relative luminance (WCAG, sRGB) is:

| Colour | Luminance | Contrast on white |
|---|---|---|
| `#1F4E79` v1 navy | 0.0712 | 8.66:1 |
| `#014C8D` Aqua Blue | 0.0710 | 8.68:1 |

They are the same weight on the page to four decimal places. Every v1
contrast decision — white text on the primary, primary text on white and
Sandstone (8.17:1), grey explanations beside it — carries over unchanged.
The swap is a hue correction, not a redesign, which is why the v1 layout
survives intact in `brand.css`.

## Yellow: where it may and may not appear

Yellow on white is **1.49:1**; on Sandstone 1.41:1. It fails every text
contrast threshold, including large-text AA (3:1). So:

- **Never as text on a light surface.** Not for a number, not for a label,
  not for a link.
- **On the Deep Blue band** it is 11.95:1 and is the highlight colour: the
  active nav underline, the headline-card accent, the figures inside the
  exec summary.
- **As a filled chip** with Deep Blue text (11.95:1): the "Internal" chip
  in the banner.
- **As the one emphasis bar in a chart** — a filled shape, not text — for
  the month or channel under discussion. One bar per chart, never two.

## v1 → brand token mapping

| v1 value | Where v1 used it | Brand token |
|---|---|---|
| `#1F4E79` | headings, tile values, `th`, pills, chart bars | `--vhpc-aqua` |
| `#f5f7fa` | page background | `--vhpc-sandstone` |
| `#1a202c` | body text | `--vhpc-deep` |
| `#64748b`, `#475569` | labels, explanations | `--vhpc-grey` |
| `#e2e8f0` | borders, grid lines | `--vhpc-rule` (`#E3E6EA`) |
| `#f59e0b` | exec-summary card accent | `--vhpc-yellow` |
| `linear-gradient(#1F4E79, #2c6ba0)` | exec-summary band | flat `--vhpc-deep` |
| `linear-gradient(#f8fafc, #e2e8f0)` | tile background | `--vhpc-aqua-06` (6% Aqua) |
| `#dc2626 / #ca8a04 / #16a34a / #0284c7` | flag rows | unchanged: `--flag-*` |
| `.delta.up / .down / .neutral` | delta colour, set by hand | `.delta-good / -bad / -flat`, set by `direction_class()` |

Gradients are gone. They photograph badly in screen-shares and add nothing
a flat band does not.

## Typography

**Inter**, self-hosted as woff2 (`assets/fonts/`), weights 400, 400 italic,
700 and 900, `font-display: swap`. Licence: SIL Open Font License 1.1, copy
in `assets/fonts/LICENSE.txt`. Fallback stack: `-apple-system,
BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial,
sans-serif` — system neo-grotesques of similar width, so a page renders
correctly before the font arrives and identically if it never does.

### Why not Acronym

The brand typeface is Acronym. Two routes were considered and rejected:

1. **Self-hosting the licensed files.** The Reserves licence, clause 7,
   forbids embedding the font software in a web page or application. A
   dashboard that serves the woff would be in breach.
2. **Adobe Fonts kit.** Permitted, but it is a runtime dependency on a
   third-party host from a page behind Cloudflare Access, it phones home
   on every load, and it is not available offline or in a saved copy.
   That is the same class of problem as a CDN script, which we also refuse.

Inter is metrically and tonally close (a neo-grotesque with a large
x-height and tabular figures), open-licensed, and can be vendored. It is a
substitution, and this paragraph is the record that it was a deliberate one.

### Specs

| Element | Weight | Case | Tracking | Size / leading |
|---|---|---|---|---|
| Page title `h1` | 900 | UPPERCASE | +0.02em | 26px / 1.15 |
| Section heading `h2` | 900 | UPPERCASE | +0.02em | 17px / 1.2 |
| Headline card figure | 900 | UPPERCASE | +0.02em | 18px / 1.25 |
| Band label | 900 | UPPERCASE | +0.02em | 11px |
| Italic subheads / explanations | 400 italic | sentence | +0.025em | 13px / 1.5 |
| Body | 400 | sentence | 0 | 14px / 1.5 |
| Tile value | 700 | — | 0 | 26px / 1.1 |
| Table head | 700 | UPPERCASE | +0.02em | 12px |

`font-variant-numeric: tabular-nums` is set on every element that carries a
figure (`[data-metric]`, `.num`, `td.num`, `time`, tile values, deltas) so
columns of numbers align and a changing figure does not shift its
neighbours. Inter's `tnum` feature is also enabled at body level.

## Charts

Chart.js 4.4.x, vendored (`assets/vendor/`, see `VERSIONS.md`). Configs are
data produced by `src/render/charts.py`; one bootstrap in `base.html`
instantiates them.

- **Single hue.** Bars and lines are Aqua Blue. A second or third series
  steps down in Aqua alpha (55%, 30%); there is no fourth. A chart that
  needs four series needs to be two charts.
- **One Yellow emphasis bar** at most, for the period or channel being
  discussed.
- **No legend where a title will do.** One series → no legend; the
  figcaption names it.
- **No donuts.** `chart_spec('donut', ...)` raises and points at sorted
  bars. Twelve slices with six under 3% is unreadable and was unreadable
  in v1.
- **No clipping.** An explicit axis max lower than the data is refused at
  spec time. v1 cut the tallest bar off and nobody noticed for a month.
- **No animation.** `animation: false` on every chart.
- Axis ticks are formatted by the bootstrap from `meta.y_format`
  (`usd`, `count`, `pct`), grid lines are `--vhpc-rule`, tick text is Grey.

## Logo

`assets/logo/vhpc-white.svg` is supplied by brand and is not fabricated
here; the header `<img>` keeps its alt text until the file lands. Exclusion
zone: **60px** clear space on all sides for the full lock-up (mark plus
wordmark), **40px** for the tight mark alone. The header band is 72px tall
with the logo at 40px, which respects the tight zone above and below; the
lock-up zone is met horizontally by the container padding plus nav gap.
Do not place the logo on Aqua, only on Deep Blue or white.

## Things this design does not do

- No dark mode. The pages are read in meetings on projectors; one
  appearance, tested once.
- No animation of any kind.
- No CSS framework. `brand.css` is the whole stylesheet.
- No percentage-point differences, anywhere. That is a methodology rule,
  but it shapes the design: a percentage tile shows a range or a relative
  change, never "+10 pts".
