"""Account vintage from the Sage sales history, and the two new NetSuite pulls."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from src.data.vintage import (LEGACY_BAND, SageHistory, acquisition_year, band_for, load_sage,
                              vintage_table)
from src.ingest import netsuite as ns


@pytest.fixture
def sage():
    return SageHistory({"0000004": 2019, "W151660": 2023, "MB01": 2021}, {})


class TestSageJoin:
    def test_repo_file_loads_and_has_the_floor(self):
        s = load_sage()
        assert len(s) > 7000
        assert s.first_sale_year["0000004"] == 2019          # Artistic Concrete, selling in 2019
        assert s.sage_id("0000004 Artistic Concrete") == "0000004"
        assert s.sage_id("W151660 Dillinger Beck") == "W151660"
        assert s.sage_id("NS-2025-0001 Brand New") is None

    def test_sage_wins_over_the_migration_date(self, sage):
        assert acquisition_year("0000004 Artistic Concrete", 2024, sage) == (2019, "sage")
        assert acquisition_year("W151660 Dillinger Beck", "2024", sage) == (2023, "sage")

    def test_netsuite_year_for_accounts_sage_never_saw(self, sage):
        assert acquisition_year("Fresh Account", 2025, sage) == (2025, "netsuite")
        with pytest.raises(ValueError):
            acquisition_year("Fresh Account", None, sage)

    def test_bands(self):
        assert band_for(2012) == LEGACY_BAND and band_for(2019) == LEGACY_BAND
        assert band_for(2020) == "2020" and band_for(2025) == "2025"

    def test_table_shares_and_per_account(self, sage):
        rows = [
            {"entityid": "0000004 Artistic Concrete", "datecreated_year": 2024, "net_revenue": "30000"},
            {"entityid": "MB01 Marble", "datecreated_year": 2024, "net_revenue": "10000"},
            {"entityid": "New One", "datecreated_year": 2025, "net_revenue": "2000"},
            {"entityid": "Refunded", "datecreated_year": 2025, "net_revenue": "-500"},   # not active; still revenue
        ]
        t = vintage_table(rows, sage)
        by = {b["band"]: b for b in t["bands"]}
        assert t["total_accounts"] == 3 and t["total_net_revenue"] == D("41500")
        assert by[LEGACY_BAND]["accounts"] == 1 and by[LEGACY_BAND]["revenue_per_account"] == D("30000")
        assert by["2025"]["accounts"] == 1 and by["2025"]["net_revenue"] == D("1500")
        assert by[LEGACY_BAND]["share_of_revenue_pct"].quantize(D("0.1")) == D("72.3")
        assert t["matched_sage"] == 2 and t["matched_netsuite"] == 2


class TestNewPulls:
    def test_revenue_total_is_one_row(self):
        calls = []

        def executor(sql):
            calls.append(sql)
            return [{"net_revenue": "1234567.89", "customers_transacting": "410", "transactions": "980"}]
        a = ns.NetSuiteAdapter(executor)
        pull = a.pull_revenue_total("2026-08")
        assert D(str(pull.body["net_revenue"])) == D("1234567.89") and pull.body["customers_transacting"] == 410
        assert "2026-08-01" in calls[0] and "2026-09-01" in calls[0]
        assert "c.category       NOT IN (2, 14)" in calls[0]     # GarageExperts and Vendor excluded

    def test_revenue_total_refuses_two_rows(self):
        a = ns.NetSuiteAdapter(lambda sql: [{"net_revenue": "1"}, {"net_revenue": "2"}])
        with pytest.raises(ns.NetSuiteError):
            a.pull_revenue_total("2026-08")

    def test_vintage_accounts_keeps_the_join_keys(self):
        rows = [{"customer_id": "7", "entityid": "0000004 Artistic Concrete", "datecreated_year": "2024",
                 "firstorder_year": "2024", "net_revenue": "30000", "transactions": "12"}]
        a = ns.NetSuiteAdapter(lambda sql: rows)
        pull = a.pull_vintage_accounts(date(2025, 1, 1), date(2026, 1, 1), "FY2025")
        acct = pull.body["accounts"][0]
        assert acct["entityid"] == "0000004 Artistic Concrete" and acct["datecreated_year"] == 2024
        assert pull.body["window"] == "FY2025"
