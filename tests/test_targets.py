"""The company target and pace arithmetic.

Leadership's 19% is growth in TOTAL company NET revenue over 2025 (Jules,
2026-09-05). The 2026-09-04 reading that it applied to new-customer M1
revenue is recorded as superseded in the budget file and must not come back.
The Pace arithmetic is basis-agnostic; it is exercised here on the figures
the build will use once the revenue_total series is ingested, and on the
marketing frame's own numbers so the frozen-basis decision stays tested.
"""
from decimal import Decimal as D
import pytest
from src.units import Money, delta
from src.data.targets import Pace, load_target

FY25_TOTAL = D("20590950.41")          # every transacting account, trandate in 2025 (acquisition_vintage pull)
FY25_M1_FROZEN = D("878098.00")        # published FY2025 M1: the marketing frame's baseline, not the target
CURRENT_BASIS_FY25_M1 = D("872630.57") # what a live M1 pull returns today

# the marketing frame, for the frozen-basis regression tests
YTD26_M1_JAN_AUG = D("558475.40")
Y25_M1_SEP_DEC = D("323478.00")
M1_RUN_RATE_MAY_AUG = D("88528.85")


@pytest.fixture
def target():
    return load_target(2026)


@pytest.fixture
def pace(target):
    """A pace object on the target's own basis, with illustrative year-to-date
    figures: the real ones come from revenue_total snapshots."""
    return Pace(
        target=Money(target["target_amount"], "FY2026"),
        actual_to_date=Money("14900000", "FY2026"),
        months_elapsed=8, months_remaining=4,
        prior_year_same_remainder=Money("6900000", "FY2026"),
        run_rate=Money("1850000", "FY2026"),
    )


def test_target_is_total_revenue_19_percent_over_prior_year(target):
    assert target["basis"] == "total_net_revenue"
    assert D(str(target["growth_over_prior_year_pct"])) == D("19")
    assert D(str(target["prior_year_actual"])) == FY25_TOTAL
    assert abs(D(str(target["target_amount"])) - FY25_TOTAL * D("1.19")) < D("0.01")


def test_target_is_not_the_m1_figure(target):
    """The first reading is kept for the record and must not be the live target."""
    assert D(str(target["target_amount"])) != FY25_M1_FROZEN * D("1.19")
    sup = target["superseded_2026_09_05"]
    assert sup["basis"] == "m1_net_revenue" and D(str(sup["prior_year_actual"])) == FY25_M1_FROZEN


def test_target_names_its_series_and_refuses_to_estimate(target):
    assert "revenue_total" in target["series"]
    assert "pending" in target["series"] and "estimated" in target["series"]


def test_marketing_frame_is_declared_and_not_a_target(target):
    mf = target["marketing_frame"]
    assert mf["not_a_target"] is True
    assert "M1" in mf["name"]


def test_pace_arithmetic(pace):
    # still needed = target - actual; required per month = still needed / remaining
    assert pace.still_needed.amount == pace.target.amount - D("14900000")
    assert abs(pace.required_monthly.amount * 4 - pace.still_needed.amount) < D("0.01")
    assert pace.forecast_at_run_rate.amount == D("14900000") + D("1850000") * 4
    assert pace.gap_at_run_rate.amount == pace.target.amount - pace.forecast_at_run_rate.amount
    assert not pace.on_track


def test_required_uplift_is_the_one_delta(pace):
    expected = delta(pace.still_needed.amount, D("6900000"))
    assert pace.required_uplift_vs_prior_year == expected


def test_spend_to_close_scales_with_the_return(pace):
    assert pace.spend_to_close_gap("7.63").amount * D("7.63") == pace.gap_at_run_rate.amount
    assert pace.spend_to_close_gap("2.50").amount > pace.spend_to_close_gap("7.63").amount


def test_spend_to_close_rejects_nonsense_return(pace):
    with pytest.raises(ValueError):
        pace.spend_to_close_gap(0)


# ---------------------------------------------------------------------------
# the marketing frame keeps the frozen basis
# ---------------------------------------------------------------------------

def test_marketing_frame_m1_baseline_is_frozen_not_live():
    """A live pull returns $872,630.57 today; the year-over-year growth figure
    on the pages is formed on the published $878,098 so it does not silently
    re-baseline on every build."""
    from src.build import load_inputs
    from datetime import date
    inp = load_inputs(date(2026, 9, 5), log=lambda s: None)
    fy25 = sum((inp.cohorts[f"2025-{m:02d}"].m1_net for m in range(1, 13)), D(0))
    assert fy25 == FY25_M1_FROZEN
    assert fy25 != CURRENT_BASIS_FY25_M1


def test_marketing_frame_pace_arithmetic_on_m1():
    """The M1 numbers from the first draft still compute; they now describe
    marketing's contribution, not the target."""
    p = Pace(Money(FY25_M1_FROZEN * D("1.19"), "FY2026"), Money(YTD26_M1_JAN_AUG, "FY2026"), 8, 4,
             Money(Y25_M1_SEP_DEC, "FY2026"), Money(M1_RUN_RATE_MAY_AUG, "FY2026"))
    assert abs(p.forecast_at_run_rate.amount - D("912590.80")) < D("1")
    assert abs(delta(p.forecast_at_run_rate.amount, FY25_M1_FROZEN) - D("3.9")) < D("0.1")
