---
period: 2026-08
dashboard: sales
prepared: 2026-09-05
audience: "Alexis, Dan, Parker, and sales leadership. Nothing here should need marketing context to read."
claims: {}
not_carried_forward:
  - "v1 Sales: the static tile reading '54% · 12-mo avg 42% · +12%'. Twelve is the raw difference of two percentages wearing a percent sign; the relative change is about +29%. All deltas here are relative."
  - "v1 Sales: two different 12-month averages for the same metric on one page (86 vs 74 customers; $791 vs $1,180 average deal). One source now."
  - "v1 Sales: 'March 2026 hit 55% phone capture — the year's high' while the same page called July's 55.7% a year-high. One series, one claim."
---

# Marketing pipeline for sales — August 2026

## What landed in your pipeline last month

August created {{ m("aug26.lead_records") }} records, of which
{{ m("aug26.leads_assigned") }} reached one of you and
{{ m("aug26.leads_converted") }} have become customers so far
({{ m("aug26.lead_conversion") }} of records created, a figure that keeps
rising as the month ages). The Executive page counts only the
{{ m("aug26.new_customers") }} who bought within their first month; the wider
count is the one that matters here, because a sale that closes in the second
month is still your sale.

A note on reading the conversion column. The counts are small enough that
differences of ten or fifteen percent between reps are usually noise, not
performance. Where the spread is inside the noise band the table says so
rather than ranking anyone. Every rate shows the number of leads it was
calculated on.

**The unassigned gap.** {{ m("aug26.unassigned_records") }} records
({{ m("aug26.unassigned_share") }} of the month) reached no rep. Over the
last fourteen months, unassigned records have converted at
{{ m("r14.unassigned_rate") }} — {{ m("r14.unassigned_conversions") }}
customers from {{ m("r14.unassigned_records") }} records — against
{{ m("r14.assigned_rate") }} for records routed to a rep. Unassigned records
arrive email-only and almost never carry a phone number. This is a NetSuite
routing-automation gap, not a rep issue, and it is the single largest lever on
the pipeline that costs no media money. Reconciling it is manual work on the
marketing side; it is in progress.

## Lead quality

Two phone-capture figures are shown, and they are different things. The
**all records** figure ({{ m("aug26.phone_capture") }}) counts every record
created. The **routed to a rep** figure ({{ m("aug26.phone_capture_assigned") }})
counts only records that reached one of you — and it is much higher, because
the unassigned records are the ones without phone numbers. The blended number
understates what you actually receive. Email capture is
{{ m("aug26.email_capture") }} across all records.

## Where new business is being created

Customer count and revenue by default ship-to state, twelve months ending
August: {{ m("geo.total_customers") }} customers in the window,
{{ m("geo.no_state_customers") }} of them with no state on file. First-ninety-
days revenue is shown only for customers whose ninety-day window has closed,
so the newest cohorts are not in that column.

## Twelve-month context

Over the last twelve months {{ m("r12.lead_records") }} records were created
and {{ m("r12.leads_converted") }} have become customers
({{ m("r12.lead_conversion") }}); over the last three months the figures are
{{ m("r3.lead_records") }} and {{ m("r3.leads_converted") }}
({{ m("r3.lead_conversion") }}). The three-month rate will rise as those
records age, so a lower three-month figure is not on its own a warning.

## Where marketing and sales can grow business together

- **Routing.** Every record that reaches no rep is a lead nobody calls. Fixing
  the routing rule is marketing's to do and sales' to benefit from.
- **Phone numbers.** Records that arrive with a phone number convert; records
  that do not, mostly do not. Where a form or a channel produces email-only
  records, tell us which one.
- **Second orders.** Most second orders arrive within ninety days of the
  first. If you know a first-time buyer has gone quiet at the thirty-day
  mark, that is the moment a call helps most.
