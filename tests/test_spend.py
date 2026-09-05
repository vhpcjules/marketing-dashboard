"""Spend layer: fixture reproduction, the three bases, budget reconciliation."""
import pytest
from decimal import Decimal
from src.data.spend import SpendData, Basis, Efficiency, price_ask, in_scope
from src.units import Money, delta

FIXTURE_2026_JAN_JUL = {
    "2026-01": 37613, "2026-02": 23870, "2026-03": 35920, "2026-04": 9223,
    "2026-05": 10367, "2026-06": 9894, "2026-07": 10004,
}
FIXTURE_JAN_JUL_TOTAL = 136891


@pytest.fixture(scope="module")
def sd():
    return SpendData.load(2026)


class TestGLScope:
    def test_marketing_accounts_in_scope(self):
        assert in_scope("66212.0016") and in_scope("66215.001")

    def test_naf_excluded(self):
        # 96212.* is the GarageExperts franchisee fund, not ours.
        assert not in_scope("96212.0016")

    def test_unrelated_excluded(self):
        assert not in_scope("50100")


class TestFixtureReproduction:
    """Section 7.6 spend figures, from the committed GL snapshot."""

    def test_each_month_as_posted(self, sd):
        series = sd.monthly(Basis.AS_POSTED)
        for month, want in FIXTURE_2026_JAN_JUL.items():
            got = series[month].amount
            assert abs(got - Decimal(want)) < 1, f"{month}: {got} != {want}"

    def test_jan_jul_total_as_posted(self, sd):
        total = sd.window("2026-01", "2026-07", Basis.AS_POSTED)
        assert abs(total.amount - Decimal(FIXTURE_JAN_JUL_TOTAL)) < Decimal("0.25")

    def test_no_out_of_scope_accounts_in_snapshot(self, sd):
        for month, accts in sd.postings.items():
            for a in accts:
                assert in_scope(a), f"{a} in {month}"


class TestAugustIsNotNegative:
    """The landmine: raw August GL nets to -$9,493."""

    def test_as_posted_august_is_negative(self, sd):
        assert sd.monthly(Basis.AS_POSTED)["2026-08"].amount < 0

    def test_true_operating_august_is_real_spend(self, sd):
        aug = sd.monthly(Basis.TRUE_OPERATING)["2026-08"].amount
        assert abs(aug - Decimal("8489.18")) < Decimal("0.01"), aug

    def test_efficiency_on_true_basis_is_sane(self, sd):
        """A negative spend basis would invert every efficiency metric."""
        aug = sd.monthly(Basis.TRUE_OPERATING)["2026-08"]
        assert aug.amount > 0
        eff = Efficiency("2026-08", aug, Money(50000, "2026-08"), 60)
        assert eff.return_per_dollar.value > 0
        assert eff.cost_per_customer.amount > 0


class TestCreditTreatment:
    """Two August credits, two different rules. Final treatment per Jules 2026-09-05."""

    def test_seo_misbooking_restates_its_original_months(self, sd):
        t = sd.monthly(Basis.TRUE_OPERATING); p = sd.monthly(Basis.AS_POSTED)
        assert abs((p["2026-03"].amount - t["2026-03"].amount) - Decimal("6500")) < Decimal("0.01")
        assert abs((p["2026-04"].amount - t["2026-04"].amount) - Decimal("2953.75")) < Decimal("0.01")

    def test_august_holds_only_real_august_spend(self, sd):
        """Both credits are lifted out of the month they merely landed in.
        August's true spend is $8,489.18 and it is used for every metric."""
        assert abs(sd.monthly(Basis.TRUE_OPERATING)["2026-08"].amount - Decimal("8489.18")) < Decimal("0.01")

    def test_agency_credit_is_prior_year_pending_detail(self, sd):
        c = next(c for c in sd.corrections if c["account"] == "66212.0002")
        assert c["affects_monthly_measurement"] is False
        assert "trying to get detail" in c["narrative"].lower()

    def test_agency_credit_touches_no_2026_figure(self, sd):
        assert abs(sd.window("2026-01", "2026-07", Basis.TRUE_OPERATING).amount - Decimal("127437.03")) < Decimal("0.05")
        assert abs(sd.window("2026-01", "2026-08", Basis.TRUE_OPERATING).amount - Decimal("135926.21")) < Decimal("0.05")
        assert sd.unattributed_corrections() == 0 and sd.monthly_sum_gap() == 0

    def test_monthly_series_sums_to_the_window(self, sd):
        series = sd.monthly(Basis.TRUE_OPERATING)
        msum = sum((v.amount for k, v in series.items() if k <= "2026-08"), Decimal(0))
        assert abs(msum - sd.window("2026-01", "2026-08", Basis.TRUE_OPERATING).amount) < Decimal("0.01")

    def test_no_month_is_negative(self, sd):
        for month, m in sd.monthly(Basis.TRUE_OPERATING).items():
            assert m.amount >= 0, f"{month}: {m.amount}"

    def test_annual_ledger_keeps_the_credit(self, sd):
        ledger = sd.window("2026-01", "2026-08", Basis.ANNUAL_LEDGER).amount
        true_ = sd.window("2026-01", "2026-08", Basis.TRUE_OPERATING).amount
        assert abs(ledger - Decimal("127397.34")) < Decimal("0.05")
        assert abs((true_ - ledger) - Decimal("8528.87")) < Decimal("0.05")

    def test_frozen_and_open_months(self, sd):
        assert sd.frozen_months() == [f"2026-0{i}" for i in range(1, 8)]
        assert sd.open_months() == ["2026-08"]


class TestBudgetReconciliation:
    def test_annual_approved_total(self, sd):
        assert abs(Decimal(str(sd.budget["_meta"]["annual_total"])) - Decimal("206345.60")) < Decimal("0.01")

    def test_jan_jul_budget_is_not_v1s_number(self, sd):
        b = sd.budget_window("2026-01", "2026-07").amount
        assert abs(b - Decimal("130845.60")) < Decimal("0.05"), b
        assert abs(b - Decimal("170068")) > Decimal("1000"), "v1's phantom budget resurfaced"

    def test_jan_jul_is_over_budget_not_under(self, sd):
        b = sd.budget_window("2026-01", "2026-07").amount
        a = sd.window("2026-01", "2026-07", Basis.AS_POSTED).amount
        assert a > b, "sign flip: v1 reported 20% under, truth is over"
        assert abs((a - b) - Decimal("6045.18")) < Decimal("0.5")

    def test_line_items_sum_to_stated_totals(self, sd):
        """The assertion nothing in v1 performed."""
        rows = sd.budget_vs_actual("2026-01", "2026-07")
        bud = sum((r["budget"].amount for r in rows), Decimal(0))
        act = sum((r["actual"].amount for r in rows), Decimal(0))
        assert abs(bud - sd.budget_window("2026-01", "2026-07").amount) < Decimal("0.05")
        assert abs(act - sd.window("2026-01", "2026-07", Basis.AS_POSTED).amount) < Decimal("0.05")

    def test_every_dollar_of_actual_is_attributed(self, sd):
        """v1 dropped $41,777 by listing 6 of 10 rows."""
        rows = sd.budget_vs_actual("2026-01", "2026-07")
        act = sum((r["actual"].amount for r in rows), Decimal(0))
        raw = sum(
            (v for m, accts in sd.postings.items() if m <= "2026-07" for v in accts.values()),
            Decimal(0),
        )
        assert abs(act - raw) < Decimal("0.01")

    def test_unbudgeted_lines_are_flagged_not_hidden(self, sd):
        rows = sd.budget_vs_actual("2026-01", "2026-07")
        unb = {r["account"] for r in rows if r["unbudgeted"]}
        assert {"66212.0004", "66212.0013", "66212.0020", "66215.001"} <= unb
        released = sum((r["actual"].amount for r in rows if r["unbudgeted"]), Decimal(0))
        assert abs(released - Decimal("53826.53")) < Decimal("0.5"), released


class TestWorldOfConcreteCancellation:
    def test_cancelled_months_are_zeroed(self, sd):
        vals = sd.budget_monthly("66212.0007", honour_cancellations=True)
        for idx in (7, 10, 11):   # Aug, Nov, Dec
            assert vals[idx] == 0
        assert vals[0] == Decimal("20000")   # Jan already spent, untouched

    def test_amount_released(self, sd):
        assert sd.released_by_cancellation().amount == Decimal("24500")

    def test_cancellation_does_not_disturb_closed_months(self, sd):
        """Jan-Jul budget must be identical either way - only Aug+ was cancelled."""
        with_c = sd.budget_window("2026-01", "2026-07", True).amount
        without = sd.budget_window("2026-01", "2026-07", False).amount
        assert with_c == without

    def test_full_year_plan_drops_by_the_released_amount(self, sd):
        full = sd.budget_window("2026-01", "2026-12", False).amount
        eff = sd.budget_window("2026-01", "2026-12", True).amount
        assert abs((full - eff) - Decimal("24500")) < Decimal("0.05")
        assert abs(eff - Decimal("181845.60")) < Decimal("0.05"), eff


class TestAgencySurcharge:
    def test_september_ask_is_priced_with_agency_fees(self, sd):
        """v1 priced the 90-day retargeting run at $3,000. It is $3,600."""
        ask = price_ask(1000, 3, sd.budget, "Google retargeting, 90 days")
        assert ask["media"].amount == Decimal("3000")
        assert ask["agency_surcharge"].amount == Decimal("600")
        assert ask["all_in"].amount == Decimal("3600")
        assert ask["monthly_all_in"].amount == Decimal("1200.0")

    def test_focus_group_comparison_uses_the_all_in_figure(self, sd):
        ask = price_ask(1000, 3, sd.budget)
        cheaper = delta(1750, ask["all_in"].amount)
        assert abs(cheaper - Decimal("-51.4")) < Decimal("0.5"), cheaper

    def test_both_asks_fit_inside_the_released_trade_show_budget(self, sd):
        """Sep-Dec retargeting all-in, plus the focus group, vs WOC money."""
        ask = price_ask(1000, 4, sd.budget)          # Sep, Oct, Nov, Dec
        total = ask["all_in"].amount + Decimal("1750")
        assert total == Decimal("6550")
        assert total < sd.released_by_cancellation().amount


class TestEfficiencyOnTrueBasis:
    def test_jan_jul_2026(self, sd):
        eff = Efficiency(
            "Jan-Jul 2026",
            sd.window("2026-01", "2026-07", Basis.TRUE_OPERATING, "Jan-Jul 2026"),
            Money(473352, "Jan-Jul 2026"),
            447,
        )
        assert eff.return_per_dollar.per_dollar == "$3.71"
        assert eff.cost_per_customer.usd0 == "$285"
        assert abs(eff.spend_share_of_revenue - Decimal("26.9")) < Decimal("0.1")
        assert eff.avg_first_order.usd0 == "$1,059"

    def test_2025_comparison_unchanged(self):
        eff = Efficiency("Jan-Jul 2025", Money(326229, "Jan-Jul 2025"),
                         Money(481628, "Jan-Jul 2025"), 552)
        assert eff.return_per_dollar.per_dollar == "$1.48"
        assert eff.cost_per_customer.usd0 == "$591"

    def test_yoy_spend_change_on_true_basis(self, sd):
        """2025 is uncorrected: the agency credit has no detail yet."""
        true_jj = sd.window("2026-01", "2026-07", Basis.TRUE_OPERATING).amount
        assert abs(delta(true_jj, 326229) - Decimal("-60.9")) < Decimal("0.1")


class TestGLScopePrecedence:
    """The NAF exclusion must be load-bearing, not incidentally redundant.

    Mutation testing caught this: with the default prefix sets, '96212.0016'
    fails the include test anyway, so deleting GL_EXCLUDE_PREFIXES changed
    nothing and the original test passed for the wrong reason. These tests
    exercise the precedence property itself.
    """

    def test_exclusion_beats_inclusion(self):
        loose = ("9621", "6621")
        assert in_scope("96212.0016", include=loose, exclude=("96212",)) is False
        assert in_scope("96212.0016", include=loose, exclude=()) is True, \
            "if this is False the test proves nothing - fix the fixture"

    def test_our_accounts_survive_a_loose_include(self):
        assert in_scope("66212.0016", include=("6621",), exclude=("96212",)) is True

    def test_substring_style_include_still_cannot_admit_naf(self):
        assert in_scope("96212.0007", include=("6212", "6215"), exclude=("96212",)) is False


class TestQueryTextAssertions:
    """Section 7.1: assert on the query text, not just the results."""

    @staticmethod
    def _sql(name, executable_only=False):
        from src.data.spend import REPO_ROOT
        text = (REPO_ROOT / "src" / "data" / "queries" / name).read_text()
        if executable_only:
            # Scan executable SQL only. The comments deliberately quote the
            # dangerous patterns in order to warn about them, so a naive
            # whole-file scan flags the warning as the offence - the same
            # trap as scanning raw HTML for "pt" and hitting "font-size:12pt".
            text = "\n".join(
                line for line in text.splitlines()
                if not line.lstrip().startswith("--")
            )
        return text

    def test_net_revenue_has_itemtype_clause(self):
        assert "i.itemtype   IS NOT NULL" in self._sql("net_revenue_monthly.sql")

    def test_net_revenue_has_all_four_transaction_types(self):
        sql = self._sql("net_revenue_monthly.sql")
        for t in ("CustInvc", "CashSale", "CustCred", "CustRfnd"):
            assert t in sql, f"missing transaction type {t} - returns would be ignored"

    def test_net_revenue_flips_the_sign(self):
        assert "SUM(-tl.foreignamount)" in self._sql("net_revenue_monthly.sql")

    def test_net_revenue_coalesces(self):
        assert "COALESCE(SUM(" in self._sql("net_revenue_monthly.sql")

    def test_net_revenue_filters_subsidiary(self):
        assert "t.subsidiary = :subsidiary_id" in self._sql("net_revenue_monthly.sql")

    def test_net_revenue_excludes_mainline_and_taxline(self):
        sql = self._sql("net_revenue_monthly.sql")
        assert "tl.mainline  = 'F'" in sql and "tl.taxline   = 'F'" in sql

    def test_spend_query_excludes_naf(self):
        assert "NOT LIKE '96212%'" in self._sql("marketing_spend_monthly.sql")

    def test_spend_query_uses_anchored_not_substring_patterns(self):
        """LIKE '%6212%' would match the NAF accounts too."""
        sql = self._sql("marketing_spend_monthly.sql", executable_only=True)
        for bad in ("LIKE '%6212", "LIKE '%6215"):
            assert bad not in sql, f"substring pattern {bad!r} would admit NAF accounts"


    def test_naf_prefix_is_declared_in_config(self):
        """Makes GL_EXCLUDE_PREFIXES load-bearing.

        TestGLScopePrecedence proves the exclusion MECHANISM works; this
        proves the NAF is actually registered in it. Both are needed - the
        mechanism test passes explicit prefixes, so on its own it would not
        notice the module constant being emptied.
        """
        from src.data.spend import GL_EXCLUDE_PREFIXES
        assert "96212" in GL_EXCLUDE_PREFIXES, \
            "the GarageExperts franchisee fund is no longer excluded"
