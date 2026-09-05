"""The narrative layer: prose resolved by the build, never typed numbers.

Front-matter claims are arithmetic over metric ids with an assertion; the
body is Markdown with m()/d()/c() references; sections are placed by slug
and an unplaced one fails the page.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from src.render import MetricRegistry, render
from src.render.narrative import (ClaimExprError, Narrative, NarrativeError, RenderedNarrative,
                                  evaluate, load_narrative, slugify)
from src.render.registry import ClaimError
from src.units import Count, Money, Pct
from src.validate import narrative as vnarr
from src.validate.dom import parse

FRONT = """---
period: 2026-08
dashboard: executive
claims:
  legacy:
    expr: "fy25.a / fy25.b"
    assert: "between(9, 13)"
    render: "{:.0f}×"
  plan:
    expr: "b26.plan - b26.actual"
    assert: "nonzero"
    render:
      positive: "${:,.0f} under plan"
      negative: "${:,.0f} over plan"
not_carried_forward:
  - "v1 said $33,177 under."
---
"""

BODY = """
# Title

Intro with {{ m("aug26.new_customers") }} & an ampersand.

## Are we growing?

{{ m("aug26.new_customers") }} customers, {{ d("aug26.new_customers", "jul26.new_customers") }} on the month.
Legacy {{ c("legacy") }}; budget {{ c("plan") }}.

### Sub heading

- {{ m("fy25.a") }}

## What changed in how we count

Second section.
"""


@pytest.fixture
def reg():
    r = MetricRegistry()
    r.register("aug26.new_customers", Count(87, "2026-08"), kind="count", source="t")
    r.register("jul26.new_customers", Count(70, "2026-07"), kind="count", source="t")
    r.register("fy25.a", Money("25574", "FY2025"), kind="currency", source="t")
    r.register("fy25.b", Money("2316", "FY2025"), kind="currency", source="t")
    r.register("b26.plan", Money("100", "x"), kind="currency", source="t")
    r.register("b26.actual", Money("130", "x"), kind="currency", source="t")
    return r


def _nar(text=FRONT + BODY, period="2026-08", dashboard="executive"):
    return Narrative.parse(text, Path("test.md"), period=period, dashboard=dashboard)


class TestExpressions:
    look = staticmethod(lambda mid: {"a.x": Decimal(10), "a.y": Decimal(4)}[mid])

    def test_arithmetic_and_metric_ids(self):
        assert evaluate("a.x / a.y", self.look) == Decimal("2.5")
        assert evaluate("(a.x - a.y) * 2 + 1", self.look) == Decimal(13)
        assert evaluate("-a.y", self.look) == Decimal(-4)

    def test_delta_is_the_one_delta(self):
        assert evaluate("delta(a.x, a.y)", self.look) == Decimal(150)
        assert evaluate("abs(delta(a.y, a.x))", self.look) == Decimal(60)

    @pytest.mark.parametrize("bad", [
        "__import__('os')", "a.x ** 2", "a.x if a.y else 0", "x", "open('f')",
        "[a.x]", "a.x, a.y", "lambda: 1", "'str'", "a.x // a.y", "max(a=1)",
    ])
    def test_everything_else_is_refused(self, bad):
        with pytest.raises(ClaimExprError):
            evaluate(bad, self.look)

    def test_attribute_chains_are_metric_ids_not_python(self):
        # a dotted name is looked up as a metric id; the registry decides whether it exists
        with pytest.raises(KeyError):
            evaluate("a.x.__class__", self.look)

    def test_division_by_zero_is_named(self):
        with pytest.raises(ClaimExprError, match="divides by zero"):
            evaluate("a.x / (a.y - 4)", self.look)


class TestFrontMatter:
    def test_period_and_dashboard_must_match(self):
        with pytest.raises(NarrativeError, match="period"):
            _nar(period="2026-09")
        with pytest.raises(NarrativeError, match="dashboard"):
            _nar(dashboard="sales")

    def test_missing_front_matter(self):
        with pytest.raises(NarrativeError, match="front-matter"):
            _nar("# no front matter\n")

    def test_claim_needs_all_three_parts(self):
        text = FRONT.replace('    render: "{:.0f}×"\n', "")
        with pytest.raises(NarrativeError, match="legacy"):
            _nar(text)

    def test_unknown_assertion_refused(self, reg):
        n = _nar(FRONT.replace('assert: "between(9, 13)"', 'assert: "roughly(11)"') + BODY)
        with pytest.raises(NarrativeError, match="assertion"):
            n.register_claims(reg)


class TestClaims:
    def test_claims_render_and_assert(self, reg):
        n = _nar()
        n.register_claims(reg)
        assert str(reg.c("legacy")) == '<span data-claim="legacy">11×</span>'
        assert "$30 over plan" in str(reg.c("plan"))

    def test_sign_mapping_flips_with_the_value(self, reg):
        reg._metrics.pop("b26.actual")
        reg.register("b26.actual", Money("70", "x"), kind="currency", source="t")
        n = _nar()
        n.register_claims(reg)
        assert "$30 under plan" in str(reg.c("plan"))

    def test_failed_assertion_fails_the_render(self, reg):
        reg._metrics.pop("fy25.b")
        reg.register("fy25.b", Money("100", "FY2025"), kind="currency", source="t")   # multiple becomes 255x
        n = _nar()
        n.register_claims(reg)
        with pytest.raises(ClaimError):
            n.render(reg)

    def test_unknown_metric_in_claim_is_a_build_failure(self, reg):
        n = _nar(FRONT.replace("fy25.a / fy25.b", "fy25.a / fy25.nope") + BODY)
        n.register_claims(reg)
        with pytest.raises(KeyError, match="fy25.nope"):
            n.render(reg)


class TestRendering:
    def test_sections_split_at_h2_and_keep_metric_spans(self, reg):
        n = _nar()
        n.register_claims(reg)
        rn = n.render(reg)
        assert set(rn.sections) == {"are-we-growing", "what-changed-in-how-we-count"}
        sec = str(rn.section("are-we-growing"))
        assert 'data-narrative="are-we-growing"' in sec
        assert 'data-metric="aug26.new_customers"' in sec
        assert 'data-delta="+24.3"' in sec and "delta-good" in sec
        assert "<h3>Sub heading</h3>" in sec and "<li>" in sec
        assert "<h1" not in str(rn.intro()) and "&amp;" in str(rn.intro())

    def test_typo_in_metric_id_fails(self, reg):
        n = _nar(FRONT + BODY.replace('m("fy25.a")', 'm("fy25.aa")'))
        n.register_claims(reg)
        with pytest.raises(KeyError, match="fy25.aa"):
            n.render(reg)

    def test_unplaced_sections_are_reported(self, reg):
        n = _nar()
        n.register_claims(reg)
        rn = n.render(reg)
        assert set(rn.unplaced()) == {"are-we-growing", "what-changed-in-how-we-count", "(intro)", "(not_carried_forward)"}
        rn.section("are-we-growing"); rn.intro(); rn.not_carried_forward()
        assert rn.unplaced() == ["what-changed-in-how-we-count"]
        rn.section("what-changed-in-how-we-count")
        assert rn.unplaced() == []

    def test_absent_slot_renders_empty_not_error(self, reg):
        rn = RenderedNarrative.empty("2026-08", "executive")
        assert str(rn.section("anything")) == "" and str(rn.status()) == "" and not rn.is_pending

    def test_pending_status_is_a_visible_callout(self):
        rn = RenderedNarrative.pending("2026-09", "sales", "content/2026-09/sales.md does not exist")
        html = str(rn.status())
        assert 'data-pending="narrative"' in html and "does not exist" in html
        assert rn.unplaced() == []

    def test_not_carried_forward_is_marked_retired(self, reg):
        n = _nar()
        n.register_claims(reg)
        html = str(n.render(reg).not_carried_forward())
        assert "data-retired" in html and "$33,177" in html

    def test_duplicate_slugs_refused(self, reg):
        n = _nar(FRONT + BODY + "\n## Are we growing?\n\nagain\n")
        n.register_claims(reg)
        with pytest.raises(NarrativeError, match="slug"):
            n.render(reg)


class TestLoading:
    def test_missing_file_is_none(self, tmp_path):
        assert load_narrative("2031-01", "executive", root=tmp_path) is None

    def test_repo_content_parses_for_its_own_month(self):
        for dash in ("executive", "marketing-ops", "sales"):
            n = load_narrative("2026-08", dash)
            assert n is not None and n.dashboard == dash

    def test_slugify(self):
        assert slugify("Are we growing?") == "are-we-growing"
        assert slugify("<em>What</em> changed &amp; why") == "what-changed-why"


class TestValidatorExemptions:
    """The validator rules the narrative layer relies on."""

    def test_retired_quotation_is_not_an_orphaned_number(self):
        doc = parse('<div data-retired><ol><li>v1 said $33,177 under.</li></ol></div>')
        assert vnarr.check_orphaned_numbers(doc, "x.html") == []
        doc = parse('<div><ol><li>v1 said $33,177 under.</li></ol></div>')
        assert len(vnarr.check_orphaned_numbers(doc, "x.html")) == 2   # "v1" and "$33,177"

    def test_time_element_is_a_label_not_a_stale_month(self):
        doc = parse('<p>Prepared <time datetime="2026-09-05">September 5, 2026</time></p>')
        assert vnarr.check_stale_months(doc, "x.html", "2026-08") == []
        doc = parse('<p>Prepared September 5, 2026</p>')
        assert len(vnarr.check_stale_months(doc, "x.html", "2026-08")) == 1


class TestEndToEnd:
    def test_executive_template_places_the_story(self, reg):
        """A narrative section is placed by the executive template."""
        from tests.test_render import executive_context
        r = MetricRegistry()
        ctx = executive_context(r)
        text = FRONT.replace("claims:", "claims: {}\nx:").split("not_carried_forward")[0] + "not_carried_forward: []\n---\n" \
            + "\n## Are we growing?\n\nVolume: {{ m('aug26.new_customers') }}.\n"
        n = Narrative.parse(text, Path("t.md"), period="2026-08", dashboard="executive")
        ctx["narrative"] = n.render(r)
        html = render("executive.html", ctx, registry=r)
        assert 'data-narrative="are-we-growing"' in html
        assert ctx["narrative"].unplaced() == []
