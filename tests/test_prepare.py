import json
import math
from datetime import date

import pandas as pd
import pytest

from engine import prepare
import synth


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Builds a dataset once from the made-up files and returns (rows, meta, folder)."""
    folder = tmp_path_factory.mktemp("build")
    synth.make_offer(folder / "offer.xlsx")
    synth.make_billing(folder / "billing.csv")
    synth.make_mis(folder / "mis.xlsx")
    meta = prepare.build_dataset(folder / "offer.xlsx", folder / "mis.xlsx", folder / "billing.csv",
                                 synth.MONTHS, out_dir=folder / "out")
    rows = pd.read_csv(folder / "out" / "rows.csv")
    return rows, meta, folder


def expected_monthly_revenue():
    """Average monthly billed revenue the build should end up with: ALPHA and BETA only, readable pack sizes,
    Jul-Sep only. (GAMMA is a group the MIS does not cover, DELTA has no price in the offer file.)"""
    total = 0.0
    for cid, _, grp, offices in synth.CUSTOMERS:
        if cid not in (1001, 1002):
            continue
        for _so in offices:
            for sku in synth.SKUS:
                total += 1000 * synth.net_price(sku)      # average of 900, 1000 and 1100 packs
    return total


def test_units_are_the_average_over_the_base_months_only(built):
    rows, _, _ = built
    assert set(rows["units"].round(1)) == {1000.0}      # October's 5000 must not leak in


def test_pack_size_fallback_recovers_a_line_sql_could_not_read(built):
    rows, _, _ = built
    row = rows[(rows["chain"] == "ALPHA") & (rows["sku"] == 7001) & (rows["so"] == 2001)].iloc[0]
    assert row["units"] == 1000.0                        # (900 + 1000 + 1100) / 3, with Sep recovered from the text


def test_revenue_reconciles_to_billing(built):
    rows, _, _ = built
    revenue = float((rows["units"] * rows["net_px"]).sum())
    assert math.isclose(revenue, expected_monthly_revenue(), rel_tol=1e-4)


def test_groups_the_mis_does_not_cover_are_left_out_and_counted(built):
    rows, meta, _ = built
    assert "GAMMA" not in set(rows["chain"])
    assert set(rows["grp"]) <= {"Modern Formats", "E-Commerce"}
    assert meta["quality"]["billing_revenue_outside_mis_groups_pct"] > 0


def test_lines_with_a_missing_price_are_dropped_and_counted(built):
    rows, meta, _ = built
    assert "DELTA" not in set(rows["chain"])
    assert meta["quality"]["offer_lines_dropped_missing_terms"] == len(synth.SKUS)


def test_unreadable_pack_size_is_dropped_and_counted(built):
    _, meta, _ = built
    assert meta["quality"]["billing_revenue_with_unreadable_pack_size_pct"] > 0


def test_commercial_terms_come_from_the_offer_file(built):
    rows, _, _ = built
    milk = rows[rows["sku"] == 7001].iloc[0]
    assert math.isclose(milk["mrp_x"], 50 / 1.05, rel_tol=1e-4)
    assert math.isclose(milk["trade"], 0.20, rel_tol=1e-4)
    assert math.isclose(milk["promo_rate"], 2.0 / (50 * 0.8), rel_tol=1e-4)
    assert math.isclose(milk["other_rate"], synth.OTHER_RATE, abs_tol=1e-4)    # the gap to what was billed


def test_costs_are_attributed_from_the_mis_on_the_chosen_basis(built):
    rows, _, _ = built
    row = rows.iloc[0]
    assert math.isclose(row["cogs"], synth.MIS_COGS_PER_PACK, rel_tol=1e-6)    # volume basis: Rs per pack
    assert math.isclose(row["sd"], synth.MIS_SD_PER_PACK, rel_tol=1e-6)
    assert math.isclose(row["pa"], synth.MIS_PA_PCT, rel_tol=1e-6)             # revenue basis: share of net revenue
    assert math.isclose(row["mktg"], synth.MIS_MKTG_PCT, rel_tol=1e-6)


def test_no_missing_or_infinite_numbers_reach_the_dataset(built):
    rows, _, _ = built
    numbers = rows.select_dtypes("number")
    assert not numbers.isna().any().any()
    assert not numbers.isin([math.inf, -math.inf]).any().any()


def test_meta_is_written_with_a_reconciliation(built):
    _, meta, folder = built
    assert json.loads((folder / "out" / "meta.json").read_text())["rows"] == meta["rows"]
    groups = {g["group"] for g in meta["reconciliation"]}
    assert {"All", "Modern Formats", "E-Commerce"} <= groups
    for g in meta["reconciliation"]:
        assert {l["line"] for l in g["lines"]} == set(prepare.LINES)


def test_missing_offer_column_is_named(tmp_path):
    synth.make_offer(tmp_path / "offer.xlsx")
    df = pd.read_excel(tmp_path / "offer.xlsx").drop(columns=["GST%"])
    df.to_excel(tmp_path / "bad.xlsx", index=False)
    with pytest.raises(prepare.DataError, match="GST%"):
        prepare.load_offer(tmp_path / "bad.xlsx")


def test_missing_billing_month_is_reported(tmp_path):
    synth.make_offer(tmp_path / "offer.xlsx")
    synth.make_billing(tmp_path / "billing.csv", months=["2026-07-01"])
    synth.make_mis(tmp_path / "mis.xlsx")
    with pytest.raises(prepare.DataError, match="2026-08-01"):
        prepare.build_dataset(tmp_path / "offer.xlsx", tmp_path / "mis.xlsx", tmp_path / "billing.csv",
                              ["2026-07-01", "2026-08-01"], out_dir=tmp_path / "out")


def test_a_base_month_is_required(tmp_path):
    with pytest.raises(prepare.DataError, match="base month"):
        prepare.build_dataset("x", "y", "z", [], out_dir=tmp_path)


def test_wrong_mis_sheet_name_lists_the_sheets_found(tmp_path):
    synth.make_mis(tmp_path / "mis.xlsx", sheet="Some other sheet")
    with pytest.raises(prepare.DataError, match="Some other sheet"):
        prepare.load_mis(tmp_path / "mis.xlsx")


def test_missing_mis_column_is_named(tmp_path):
    synth.make_mis(tmp_path / "mis.xlsx", drop_column="Mktg Cost")
    with pytest.raises(prepare.DataError, match="Mktg Cost"):
        prepare.load_mis(tmp_path / "mis.xlsx")


def test_billing_without_a_required_column_is_rejected(tmp_path):
    synth.make_offer(tmp_path / "offer.xlsx")
    synth.make_mis(tmp_path / "mis.xlsx")
    synth.billing_rows().drop(columns=["Variant"]).to_csv(tmp_path / "billing.csv", index=False)
    with pytest.raises(prepare.DataError, match="Variant"):
        prepare.build_dataset(tmp_path / "offer.xlsx", tmp_path / "mis.xlsx", tmp_path / "billing.csv",
                              synth.MONTHS, out_dir=tmp_path / "out")


@pytest.mark.parametrize("text,expected", [
    ("400 GMS", 0.4), ("500 ML", 0.5), ("1 KG", 1.0), ("1.5 LTR", 1.5), ("700+700ML", 1.4), ("ASSORTED", None)])
def test_pack_size_reads_common_formats(text, expected):
    got = prepare.pack_size(text)
    assert got == expected if expected is None else math.isclose(got, expected)


def test_default_base_months_skips_the_month_still_being_billed():
    months = ["2026-07-01", "2026-08-01", "2026-09-01", "2026-10-01"]
    assert prepare.default_base_months(months, today=date(2026, 10, 10)) == months[:3]
    assert prepare.default_base_months(months[:2], today=date(2026, 10, 10)) == months[:2]
    assert prepare.default_base_months([], today=date(2026, 10, 10)) == []


def _price_frames(model_px, mis_px):
    rows = pd.DataFrame({"sku": [1, 2], "SalesOfficeID": [10, 10], "grp": ["Modern Formats"] * 2,
                         "family": ["Milk", "Curd"], "name": ["A", "B"], "units": [100.0, 100.0],
                         "net_px": model_px})
    mis = pd.DataFrame({"sku": [1, 2], "SalesOfficeID": [10, 10], "grp": ["Modern Formats"] * 2,
                        "packs": [50.0, 50.0], "rev": [50.0 * p for p in mis_px]})
    return rows, mis


def test_price_check_measures_the_revenue_per_pack_gap():
    rows, mis = _price_frames([103.0, 206.0], [100.0, 200.0])          # model sells 3% above the MIS everywhere
    pc = prepare.price_check(rows, mis)
    assert math.isclose(pc["overall"]["gap_pct"], 0.03, rel_tol=1e-9)
    assert pc["matched_revenue_pct"] == 1.0
    assert pc["revenue_by_gap_size"]["1_to_5pct"] == 1.0
    assert {f["name"] for f in pc["by_family"]} == {"Milk", "Curd"}


def test_price_check_points_at_the_sku_that_differs():
    rows, mis = _price_frames([100.0, 220.0], [100.0, 200.0])          # only SKU 2 is 10% higher
    pc = prepare.price_check(rows, mis)
    assert pc["largest_gaps"][0]["sku"] == "2"
    assert math.isclose(pc["largest_gaps"][0]["gap_pct"], 0.10, rel_tol=1e-9)
    assert math.isclose(pc["largest_gaps"][0]["diff_rev"], 2000.0, rel_tol=1e-9)
    assert math.isclose(pc["revenue_by_gap_size"]["over_5pct"], 22000 / 32000, rel_tol=1e-9)


def test_price_check_ignores_lines_the_mis_does_not_have():
    rows, mis = _price_frames([100.0, 100.0], [100.0, 100.0])
    pc = prepare.price_check(rows, mis.iloc[:1])
    assert pc["matched_revenue_pct"] == 0.5


def test_build_writes_the_price_check(built):
    _, meta, _ = built
    assert meta["price_check"]["overall"] is not None
    assert json.dumps(meta)                                              # stays plain JSON
