"""Target pace on the FROZEN basis.

Per Jules 2026-09-04: keep the frozen revenue we are tracking. Closed months
use their frozen snapshot values; months never previously published (2026-08
onward) use live figures. The -1.1% drift detected on 2026-09-04 is logged
and deliberately not applied.
"""
from decimal import Decimal as D
import pytest
from src.units import Money
from src.data.targets import Pace, load_target

FY25_FROZEN = D("878098.00")          # published FY2025 M1
YTD26_JAN_AUG = D("558475.40")        # Jan-Jul frozen + Aug live
Y25_SEP_DEC = D("323478.00")
RUN_RATE_MAY_AUG = D("88528.85")
CURRENT_BASIS_FY25 = D("872630.57")   # what a live pull returns today


@pytest.fixture
def pace():
    t = load_target(2026)
    return Pace(
        target=Money(t["target_amount"], "FY2026"),
        actual_to_date=Money(YTD26_JAN_AUG, "FY2026"),
        months_elapsed=8, months_remaining=4,
        prior_year_same_remainder=Money(Y25_SEP_DEC, "FY2026"),
        run_rate=Money(RUN_RATE_MAY_AUG, "FY2026"),
    )


def test_target_is_19_percent_over_frozen_prior_year():
    t = load_target(2026)
    assert abs(D(str(t["target_amount"])) - FY25_FROZEN * D("1.19")) < D("0.01")


def test_target_uses_the_frozen_basis_not_a_live_pull():
    """Guards the decision. A live pull returns $872,630.57 today; using it
    would silently re-baseline the target every time the build runs."""
    t = load_target(2026)
    assert D(str(t["prior_year_actual"])) == FY25_FROZEN
    assert D(str(t["prior_year_actual"])) != CURRENT_BASIS_FY25


def test_both_sides_of_the_comparison_use_the_same_basis():
    """Jan-Jul 2026 is frozen too, so the YoY comparison is not mixed."""
    t = load_target(2026)
    assert "FROZEN" in t["prior_year_basis_note"]
    assert "not previously published" in t["prior_year_basis_note"]


def test_not_on_track(pace):
    assert not pace.on_track


def test_required_monthly_far_above_run_rate(pace):
    assert pace.required_monthly.amount > pace.run_rate.amount * D("1.35")
    assert abs(pace.required_monthly.amount - D("121615.31")) < D("1")


def test_required_uplift_vs_prior_year(pace):
    assert abs(pace.required_uplift_vs_prior_year - D("50.4")) < D("0.2")


def test_forecast_and_gap(pace):
    assert abs(pace.forecast_at_run_rate.amount - D("912590.80")) < D("1")
    assert abs(pace.gap_at_run_rate.amount - D("132345.82")) < D("1")


def test_forecast_growth_is_far_short_of_target(pace):
    from src.units import delta
    growth = delta(pace.forecast_at_run_rate.amount, FY25_FROZEN)
    assert abs(growth - D("3.9")) < D("0.1")
    assert growth < D("19")


def test_gap_cannot_be_closed_by_available_budget(pace):
    """WOC released $24,500 plus $5,119 running under plan."""
    available = D("29619")
    assert pace.spend_to_close_gap("3.71").amount > available
    assert pace.spend_to_close_gap("2.00").amount > available * D("2")


def test_secondary_frame_is_not_a_target():
    """Revenue-to-date is for reading ROAS, not for grading against 19%."""
    t = load_target(2026)
    assert t["secondary_frame"]["not_a_target"] is True
    assert "maturity_caveat" in t["secondary_frame"]


def test_spend_to_close_rejects_nonsense_return(pace):
    with pytest.raises(ValueError):
        pace.spend_to_close_gap(0)
