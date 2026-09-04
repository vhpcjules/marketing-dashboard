"""Unit tests for the single delta function and the unit types.

The v1 bug values are first-class test inputs here, not comments. Each
`WRONG` assertion is a point difference that v1 published with a percent
sign; the suite fails if we ever produce one again.
"""
import pytest
from decimal import Decimal
from src.units import (
    delta, Money, Pct, PctPoints, Ratio, Count,
    PointDifferenceError, UndefinedDeltaError, direction_class, arrow,
)


def approx(d, want, tol="0.05"):
    return abs(Decimal(str(d)) - Decimal(str(want))) <= Decimal(tol)


class TestDeltaAgainstV1BugValues:
    """From fixtures_known_good.json delta_assertions and channel notes."""

    def test_phone_capture_jun_to_jul(self):
        # The headline case: 45.6% -> 55.7%
        assert approx(delta(55.7, 45.6), "22.1")
        assert not approx(delta(55.7, 45.6), "10.1"), "point difference resurfaced"

    def test_conversion_jun_to_jul(self):
        assert approx(delta(26.5, 26.0), "1.9")
        assert not approx(delta(26.5, 26.0), "0.5")

    def test_ga4_engagement_rate(self):
        # 36.9% -> 52.0% is +40.9% relative, not +15.1 points
        assert approx(delta(52.0, 36.9), "40.9")
        assert not approx(delta(52.0, 36.9), "15.1")

    def test_spend_share_of_revenue(self):
        # 67.7% -> 28.9% is -57% relative, not -38.8 points
        assert approx(delta(28.9, 67.7), "-57.3")
        assert not approx(delta(28.9, 67.7), "-38.8")

    def test_reorder_rate_small_vs_mid(self):
        # 35.4% vs 49.1% is "28% less likely", not "14 points less likely"
        assert approx(delta(35.4, 49.1), "-27.9")
        assert not approx(delta(35.4, 49.1), "-13.7")

    def test_sales_static_tile_bug(self):
        # v1 Sales markup: "54%" with "12-mo avg 42% +12%" - a raw subtraction.
        assert approx(delta(54, 42), "28.6")
        assert not approx(delta(54, 42), "12")

    def test_return_per_dollar_yoy(self):
        assert approx(delta(3.46, 1.48), "133.8")

    def test_zero_baseline_is_an_error_not_a_number(self):
        with pytest.raises(UndefinedDeltaError):
            delta(5, 0)


class TestPctPointsCannotRender:
    def test_subtraction_yields_pctpoints(self):
        assert isinstance(Pct(55.7) - Pct(45.6), PctPoints)

    def test_format_raises(self):
        pp = Pct(55.7) - Pct(45.6)
        with pytest.raises(PointDifferenceError):
            f"{pp}"

    def test_str_raises(self):
        with pytest.raises(PointDifferenceError):
            str(Pct(55.7) - Pct(45.6))

    def test_fstring_with_percent_sign_raises(self):
        # The exact v1 shape: interpolate a point difference next to a % sign.
        pp = Pct(55.7) - Pct(45.6)
        with pytest.raises(PointDifferenceError):
            f"↑ {pp}% vs June"

    def test_error_names_the_right_answer(self):
        pp = Pct(55.7) - Pct(45.6)
        with pytest.raises(PointDifferenceError) as e:
            str(pp)
        assert "22.1%" in str(e.value)
        assert "45.6% -> 55.7%" in str(e.value)

    def test_repr_still_works_for_debugging(self):
        assert "10.1" in repr(Pct(55.7) - Pct(45.6))

    def test_adding_percentages_is_unavailable(self):
        with pytest.raises(TypeError):
            Pct(10) + Pct(20)


class TestMoneyRequiresPeriod:
    def test_period_required(self):
        with pytest.raises(ValueError, match="period"):
            Money(1000, "")

    def test_refuses_cross_period_arithmetic(self):
        with pytest.raises(ValueError, match="different periods"):
            Money(100, "2026-07") + Money(100, "2026-06")

    def test_formatting(self):
        assert Money(51088, "2026-07").usd0 == "$51,088"
        assert Money(51088, "2026-07").usdk == "$51K"
        assert Money(Decimal("22.796"), "2026-07").usd2 == "$22.80"

    def test_decimal_not_float(self):
        # 0.1 + 0.2 must be exactly 0.3 for cent-level cross-dashboard agreement
        total = Money("0.1", "p") + Money("0.2", "p")
        assert total.amount == Decimal("0.3")


class TestDirectionAndColourAgree:
    def test_fall_is_bad_when_higher_is_better(self):
        # v1 styled a 63% fall in average deal size green.
        assert direction_class(delta(710, 1941)) == "delta-bad"

    def test_rise_is_good_when_higher_is_better(self):
        assert direction_class(delta(55.7, 45.6)) == "delta-good"

    def test_cost_rising_is_bad(self):
        # CPM 11.61 -> 13.78 is a rise, and a rise in CPM is bad news.
        assert direction_class(delta(13.78, 11.61), higher_is_better=False) == "delta-bad"

    def test_cost_falling_is_good(self):
        assert direction_class(delta(306, 591), higher_is_better=False) == "delta-good"

    def test_arrow_matches_sign(self):
        assert arrow(delta(72, 67)) == "↑"
        assert arrow(delta(710, 1941)) == "↓"


class TestCount:
    def test_period_required(self):
        with pytest.raises(ValueError):
            Count(72, "")

    def test_formats(self):
        assert Count(2582, "14 months to 2026-07").plain == "2,582"
