"""Target pace, on live figures pulled 2026-09-04."""
from decimal import Decimal as D
import pytest
from src.units import Money
from src.data.targets import Pace, load_target

FY25 = D("872630.57")
YTD26_JAN_AUG = D("552655.74")
Y25_SEP_DEC = D("323478.73")
RUN_RATE_MAY_AUG = D("87073.88")


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


def test_target_is_19_percent_over_prior_year(pace):
    t = load_target(2026)
    assert abs(D(str(t["target_amount"])) - FY25 * D("1.19")) < D("0.01")


def test_target_uses_current_basis_not_published(pace):
    """Mixing bases would compare today's 2026 to August's 2025."""
    t = load_target(2026)
    assert abs(D(str(t["prior_year_actual"])) - FY25) < D("0.01")
    assert D(str(t["prior_year_actual"])) != D("878098")  # the published basis


def test_not_on_track(pace):
    assert not pace.on_track


def test_required_monthly_far_above_run_rate(pace):
    assert pace.required_monthly.amount > pace.run_rate.amount * D("1.35")


def test_required_uplift_vs_prior_year(pace):
    assert abs(pace.required_uplift_vs_prior_year - D("50.2")) < D("0.2")


def test_forecast_and_gap(pace):
    assert abs(pace.forecast_at_run_rate.amount - D("900951.24")) < D("1")
    assert abs(pace.gap_at_run_rate.amount - D("137479")) < D("2")


def test_gap_cannot_be_closed_by_available_budget(pace):
    """WOC released $24,500 plus $5,119 under plan."""
    available = D("29619")
    needed_at_average = pace.spend_to_close_gap("3.71").amount
    assert needed_at_average > available, "if this flips, revisit the recommendation"
    needed_at_marginal = pace.spend_to_close_gap("2.00").amount
    assert needed_at_marginal > available * D("2")


def test_spend_to_close_rejects_nonsense_return(pace):
    with pytest.raises(ValueError):
        pace.spend_to_close_gap(0)
