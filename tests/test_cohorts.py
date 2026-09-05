"""Cohort revenue and dual-basis ROAS, on live figures pulled 2026-09-04."""
from datetime import date
from decimal import Decimal as D
import pytest
from src.units import Money, PointDifferenceError
from src.data.cohorts import Cohort, CohortSet, Roas

AS_OF = date(2026, 9, 4)

# (month, customers, m1, revenue_to_date)
LIVE_2026 = [
    ("2026-01", 55, "37328.43", "124560.73"),
    ("2026-02", 69, "49840.00", "164716.89"),
    ("2026-03", 89, "63240.08", "164811.00"),
    ("2026-04", 68, "53951.73", "90489.12"),
    ("2026-05", 79, "87840.60", "141353.39"),
    ("2026-06", 68, "125590.58", "161179.42"),
    ("2026-07", 79, "49740.92", "87609.71"),
    ("2026-08", 87, "85123.40", "96501.85"),
]
SPEND_JAN_AUG = D("127397.34")   # true operating, both 2026 corrections applied


@pytest.fixture
def cs():
    return CohortSet(
        label="Jan-Aug 2026",
        cohorts=[Cohort(m, c, m1, rtd) for m, c, m1, rtd in LIVE_2026],
        spend=Money(SPEND_JAN_AUG, "Jan-Aug 2026"),
        as_of=AS_OF,
    )


class TestDualBasisRoas:
    def test_m1_roas(self, cs):
        assert cs.roas_m1.per_dollar == "$4.34"

    def test_to_date_roas_is_much_higher(self, cs):
        assert cs.roas_to_date.per_dollar == "$8.09"

    def test_m1_understates_by_about_half(self, cs):
        assert abs(cs.understatement_of_m1_roas() - D("46.4")) < D("0.1")

    def test_roas_cannot_exist_without_a_basis(self):
        with pytest.raises(ValueError):
            Roas(D("4.07"), "", "Jan-Aug 2026", D("4.3"))

    def test_roas_label_carries_basis_and_maturity(self, cs):
        lab = cs.roas_to_date.label
        assert "revenue to date" in lab and "Jan-Aug 2026" in lab and "months" in lab

    def test_the_two_bases_are_distinguishable_in_output(self, cs):
        """An M1 ROAS and a to-date ROAS must never render identically."""
        assert cs.roas_m1.label != cs.roas_to_date.label


class TestRepeatRevenue:
    def test_totals(self, cs):
        assert abs(cs.m1_net.amount - D("552655.74")) < D("0.01")
        assert abs(cs.revenue_to_date.amount - D("1031222.11")) < D("0.01")
        assert abs(cs.repeat_revenue.amount - D("478566.37")) < D("0.01")

    def test_customers(self, cs):
        assert cs.customers == 594

    def test_per_customer_roughly_doubles(self, cs):
        assert cs.m1_per_customer.usd0 == "$930"
        assert cs.to_date_per_customer.usd0 == "$1,736"


class TestMaturity:
    def test_weighted_by_customers_not_months(self, cs):
        assert abs(cs.avg_maturity_months - D("4.3")) < D("0.1")

    def test_older_cohorts_have_bigger_multiples(self, cs):
        by_month = {c.month: c for c in cs.cohorts}
        assert by_month["2026-01"].multiple.value > by_month["2026-08"].multiple.value

    def test_the_maturity_curve(self, cs):
        """1 month -> 1.13x, 8 months -> 3.34x. Publishing a young cohort's
        multiple next to an old one's without this context misleads."""
        by_month = {c.month: c for c in cs.cohorts}
        assert by_month["2026-08"].multiple.multiple == "1.13x"
        assert by_month["2026-01"].multiple.multiple == "3.34x"


class TestImpossibleDataIsRejected:
    def test_revenue_to_date_below_m1_raises(self):
        with pytest.raises(ValueError, match="below M1"):
            Cohort("2026-01", 55, "37328.43", "10000.00")

    def test_equal_is_allowed(self):
        """A brand-new cohort can legitimately have produced nothing beyond M1."""
        c = Cohort("2026-08", 87, "85123.40", "85123.40")
        assert c.repeat_revenue.amount == 0
        assert c.multiple.multiple == "1.00x"


def _fy2025_spend():
    """Read FY2025 spend from the snapshot rather than hardcoding it.

    A hardcoded literal is how a period mismatch got in here in the first
    place: Jan-Jul 2025 spend ($317,700) was divided into FULL-YEAR 2025
    revenue, overstating ROAS by about 1.8x. Money's period label did not
    catch it, because the label read "FY2025" while the value was seven
    months - the label discipline catches mislabelled combinations, not a
    wrong value under a correct label. Deriving the figure from data is the
    guard that actually works.
    """
    import json
    from src.data.spend import REPO_ROOT
    p = REPO_ROOT / "data" / "snapshots" / "2025-12" / "netsuite_marketing_spend_annual.json"
    return D(str(json.loads(p.read_text())["true_operating"]))


class TestMaturityContrastWith2025:
    """FY2025: 1,120 customers, $872,631 M1, $4,442,363 to date."""

    @pytest.fixture
    def cs25(self):
        return CohortSet(
            label="FY2025",
            cohorts=[Cohort("2025-01", 1120, "872630.57", "4442362.80")],
            spend=Money(_fy2025_spend(), "FY2025"),
            as_of=AS_OF,
        )

    def test_spend_is_full_year_not_seven_months(self):
        assert _fy2025_spend() == D("591782.91")
        assert _fy2025_spend() != D("317700.13"), "Jan-Jul spend crept back in"

    def test_full_maturity_multiple(self, cs25):
        assert cs25.cohorts[0].multiple.multiple == "5.09x"

    def test_roas_both_bases(self, cs25):
        assert cs25.roas_m1.per_dollar == "$1.47"
        assert cs25.roas_to_date.per_dollar == "$7.51"

    def test_2026_already_beats_2025_at_a_third_of_the_maturity(self, cs, cs25):
        """The strongest signal available. The 2026 cohorts have already
        passed FY2025's full-year revenue-to-date ROAS, at 4.3 months of
        average maturity against 14.3."""
        assert cs.roas_to_date.value > cs25.roas_to_date.value
        assert cs.avg_maturity_months < cs25.avg_maturity_months / 3
