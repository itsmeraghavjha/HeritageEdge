import json
import math
from datetime import date, datetime

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


def _mix_frames(other_px):
    everyone = pd.DataFrame({"sku": [1, 1], "SalesOfficeID": [10, 10], "grp": ["Modern Formats"] * 2,
                             "packs": [90.0, 10.0], "rev": [90 * 100.0, 10 * other_px], "in_offer": [True, False]})
    mis = pd.DataFrame({"sku": [1], "SalesOfficeID": [10], "grp": ["Modern Formats"], "packs": [100.0],
                        "rev": [90 * 100.0 + 10 * other_px]})
    return everyone, mis


def test_mix_check_finds_that_other_customers_pay_less():
    everyone, mis = _mix_frames(60.0)            # the MIS averages in customers who pay 40% less
    mx = prepare.mix_check(everyone, mis)
    assert math.isclose(mx["all_customers"]["gap_pct"], 0.0, abs_tol=1e-12)     # billing and MIS agree
    assert math.isclose(mx["offer_customers"]["gap_pct"], 100 / 96 - 1, rel_tol=1e-9)   # MIS price is 96, they pay 100
    assert math.isclose(mx["other_customers_share_pct"], 600 / 9600, rel_tol=1e-9)


def test_mix_check_shows_a_definition_gap_when_all_billing_differs():
    everyone, mis = _mix_frames(100.0)
    mis["rev"] = mis["rev"] * 0.97               # the MIS books 3% less revenue on the same packs
    mx = prepare.mix_check(everyone, mis)
    assert math.isclose(mx["all_customers"]["gap_pct"], 1 / 0.97 - 1, rel_tol=1e-9)
    assert math.isclose(mx["offer_customers"]["gap_pct"], mx["other_customers"]["gap_pct"], rel_tol=1e-9)


def test_build_writes_the_mix_check(built):
    _, meta, _ = built
    assert meta["price_check"]["mix"]["all_customers"] is not None


def test_mix_check_compares_pack_counts():
    everyone, mis = _mix_frames(100.0)
    assert math.isclose(prepare.mix_check(everyone, mis)["packs"]["ratio"], 1.0)
    mis["packs"] = mis["packs"] * 2                # the MIS counts in a different unit
    assert math.isclose(prepare.mix_check(everyone, mis)["packs"]["ratio"], 0.5)


# ---------- the MIS is cut to the base months ----------
def _build_with_mis(tmp_path, **mis_kw):
    synth.make_offer(tmp_path / "offer.xlsx")
    synth.make_billing(tmp_path / "billing.csv")
    synth.make_mis(tmp_path / "mis.xlsx", **mis_kw)
    return prepare.build_dataset(tmp_path / "offer.xlsx", tmp_path / "mis.xlsx", tmp_path / "billing.csv",
                                 synth.MONTHS, out_dir=tmp_path / "out"), pd.read_csv(tmp_path / "out" / "rows.csv")


def test_costs_ignore_mis_months_outside_the_base_months(built):
    rows, meta, _ = built
    assert math.isclose(rows["cogs"].iloc[0], synth.MIS_COGS_PER_PACK, rel_tol=1e-6)   # April's double COGS is not in it
    assert meta["mis_months"]["used"] == synth.MONTHS
    assert "2026-04-01" in meta["mis_months"]["available"]
    assert meta["mis_months"]["year_known"] is True
    assert meta["mis_months"]["month_column"] == "Month"


def test_reconciliation_compares_with_the_same_months(built):
    _, meta, _ = built
    cogs = next(l for l in meta["reconciliation"][0]["lines"] if l["line"] == "cogs")
    assert math.isclose(cogs["mis_pct"], synth.MIS_COGS_PER_PACK / 30.0, rel_tol=1e-6)   # not pulled up by April


def test_a_base_month_the_mis_does_not_have_stops_the_build(tmp_path):
    with pytest.raises(prepare.DataError, match=r"Sep 2026.*It has: Jul 2026, Aug 2026"):
        _build_with_mis(tmp_path, months=(("Jul", 1.0), ("Aug", 1.0)))


def test_mis_months_given_as_dates_or_numbers_are_read(tmp_path):
    for style in ("dated", "number"):
        folder = tmp_path / style
        folder.mkdir()
        meta, rows = _build_with_mis(folder, month_style=style)
        assert meta["mis_months"]["used"] == synth.MONTHS


def test_a_mis_without_a_year_is_matched_by_month_name(tmp_path):
    meta, rows = _build_with_mis(tmp_path, drop_column="Year")
    assert meta["mis_months"]["year_known"] is False
    assert math.isclose(rows["cogs"].iloc[0], synth.MIS_COGS_PER_PACK, rel_tol=1e-6)


def test_a_mis_with_no_month_column_says_what_columns_it_has(tmp_path):
    synth.make_mis(tmp_path / "mis.xlsx", drop_column="Month")
    with pytest.raises(prepare.DataError, match=r"MIS_MONTH_COLUMN.*Customer Group"):
        prepare.load_mis(tmp_path / "mis.xlsx")


def test_the_month_column_can_be_named_in_the_environment(tmp_path, monkeypatch):
    synth.make_mis(tmp_path / "mis.xlsx", drop_column="Month")
    monkeypatch.setenv("MIS_MONTH_COLUMN", "filler3")
    with pytest.raises(prepare.DataError, match="could not be read"):          # found, but its cells are empty
        prepare.load_mis(tmp_path / "mis.xlsx")
    monkeypatch.setenv("MIS_MONTH_COLUMN", "No such column")
    with pytest.raises(prepare.DataError, match="no column called 'No such column'"):
        prepare.load_mis(tmp_path / "mis.xlsx")


@pytest.mark.parametrize("value,expected", [
    ("Jul", (None, 7)), ("July", (None, 7)), ("Sept", (None, 9)), ("Jul-26", (2026, 7)), ("Jul'26", (2026, 7)),
    ("July 2026", (2026, 7)), ("2026-07", (2026, 7)), ("2026-07-01", (2026, 7)), (202607, (2026, 7)),
    (7, (None, 7)), (datetime(2026, 7, 1), (2026, 7)), ("Marketing", None), ("", None), (None, None), (13, None)])
def test_parse_month(value, expected):
    assert prepare.parse_month(value) == expected


@pytest.mark.parametrize("value,expected", [
    (2026, ("cal", 2026)), ("2026", ("cal", 2026)), ("2026-27", ("fy", 2026)), ("FY26-27", ("fy", 2026)),
    ("FY 2026-27", ("fy", 2026)), ("soon", None), (12, None)])
def test_parse_year(value, expected):
    assert prepare.parse_year(value) == expected


def test_month_key_places_a_fiscal_year_month_in_the_right_calendar_year():
    assert prepare.month_key("Jul", "2026-27") == "2026-07-01"
    assert prepare.month_key("Feb", "2026-27") == "2027-02-01"
    assert prepare.month_key("Jul", 2026) == "2026-07-01"
    assert prepare.month_key("Jul") == "????-07-01"
    assert prepare.month_key("Jul", "later") is None


def test_price_check_lists_a_sku_once_even_if_its_name_is_spelt_two_ways():
    rows = pd.DataFrame({"sku": [1, 1], "SalesOfficeID": [10, 11], "grp": ["Modern Formats"] * 2,
                         "family": ["Milk"] * 2, "name": ["MILK 450 (9.9 Ltrs)", "MILK 450 (9.9"],
                         "units": [100.0, 100.0], "net_px": [110.0, 100.0]})
    mis = pd.DataFrame({"sku": [1, 1], "SalesOfficeID": [10, 11], "grp": ["Modern Formats"] * 2,
                        "packs": [50.0, 50.0], "rev": [5000.0, 5000.0]})
    top = prepare.price_check(rows, mis)["largest_gaps"]
    assert len(top) == 1 and math.isclose(top[0]["diff_rev"], 1000.0)


def test_offer_price_check_finds_chains_billed_away_from_the_offer_file():
    import pandas as pd
    # expected price = (100 x (1 - 0.2) - 5) / 1.05 = 71.43 a pack
    base = dict(sku="1", name="Ghee", mrp=100.0, trade=0.2, offer_rs=5.0, gst=0.05, packs=1000.0)
    exact = (100 * 0.8 - 5) / 1.05
    g = pd.DataFrame([dict(base, chain="A", net_px=exact),            # billed exactly as the file says
                      dict(base, chain="B", net_px=exact * 0.95),     # 5% below: an extra scheme
                      dict(base, chain="C", net_px=exact * 1.02)])    # 2% above: an offer not applied
    out = prepare.offer_price_check(g)
    assert out["gap_pct"] == pytest.approx((1 + 0.95 + 1.02) / 3 - 1)
    bands = out["revenue_by_gap"]
    assert bands["matches"] == pytest.approx(1 / 3 * exact / ((exact * (1 + 0.95 + 1.02)) / 3), rel=1e-6)
    assert bands["below_up_to_10pct"] > 0 and bands["above"] > 0 and bands["below_over_10pct"] == 0
    assert out["largest_gaps"][0]["chain"] == "B"                     # the biggest rupee difference first
    assert out["largest_gaps"][0]["diff_rev"] == pytest.approx(-0.05 * exact * 1000)


def test_the_build_records_the_offer_price_check(tmp_path):
    meta, _ = _build_with_mis(tmp_path)
    oc = meta["offer_price_check"]
    assert oc is None or {"gap_pct", "revenue_by_gap", "largest_gaps"} <= set(oc)


def _gap_lines(**over):
    import pandas as pd
    base = dict(cid=1, sku=7, BillMonth="2026-08-01", packs=1000.0, mrp=100.0, trade=0.2, offer_rs=5.0, gst=0.05,
                chain="A", name="Ghee")
    exact = (100 * 0.8 - 5) / 1.05
    rows = [dict(base, rev=exact * 1000, **over)]
    return pd.DataFrame(rows), exact


def test_gap_check_attributes_each_cause_and_the_pieces_add_up_to_the_gap():
    import pandas as pd
    from engine import gapcheck
    exact = (100 * 0.8 - 5) / 1.05
    def line(cid, month, rev, **kw):
        return dict(cid=cid, sku=7, BillMonth=month, packs=1000.0, mrp=100.0, trade=0.2, offer_rs=5.0, gst=0.05,
                    chain=f"C{cid}", name="Ghee", rev=rev, **kw)
    lines = pd.DataFrame([
        line(1, "2026-08-01", exact * 1000),                                  # billed exactly: no gap
        line(2, "2026-08-01", (100 * 0.8 - 5) / 1.12 * 1000, Tax=0.12 * (100 * 0.8 - 5) / 1.12 * 1000),   # billed at 12% GST
        line(3, "2026-08-01", exact * 1000 * 0.97), line(3, "2026-09-01", exact * 1000 * 0.97),            # steady 3% gap
        line(4, "2026-08-01", exact * 1000 * 0.99), line(4, "2026-09-01", exact * 1000 * 0.93),            # gap that moves
        line(5, "2026-08-01", exact * 1000 * 0.4),                                                         # far from the file
    ])
    out = gapcheck.explain(lines, None, 1)
    c = {x["key"]: x for x in out["causes"]}
    total = sum(x["net_pct"] for x in out["causes"])
    assert total == pytest.approx(out["total_gap_pct"])                   # nothing is lost between the causes
    assert c["gst"]["net_pct"] < 0 and c["far"]["net_pct"] < 0
    assert c["steady"]["net_pct"] == pytest.approx(-0.03 * exact * 2000 / lines["rev"].sum())
    assert c["moves"]["net_pct"] == pytest.approx(-(0.01 + 0.07) * exact * 1000 / lines["rev"].sum())
    assert out["billing_has_tax"] and out["lines_with_other_gst_rate_pct"] > 0


def test_gap_check_credits_a_second_offer_row_and_ignores_zero_tax_columns():
    import pandas as pd
    from engine import gapcheck
    lines, exact = _gap_lines(Tax=0.0)
    other_px = (100 * 0.8 - 8) / 1.05
    lines["rev"] = other_px * 1000                                       # billed at the *other* offer row's price
    rows = pd.DataFrame([dict(cid=1, sku=7, mrp=100.0, trade=0.2, offer_rs=5.0, gst=0.05),
                         dict(cid=1, sku=7, mrp=100.0, trade=0.2, offer_rs=8.0, gst=0.05)])
    out = gapcheck.explain(lines, rows, 1)
    c = {x["key"]: x for x in out["causes"]}
    assert not out["billing_has_tax"]                                    # Tax of 0 says nothing about the rate
    assert c["offer_rows"]["net_pct"] == pytest.approx(out["total_gap_pct"])
    assert abs(c["steady"]["net_pct"]) < 1e-9 and out["lines_with_several_offer_rows_pct"] == pytest.approx(1.0)


def test_the_build_records_the_gap_breakdown(tmp_path):
    meta, _ = _build_with_mis(tmp_path)
    gb = meta["gap_breakdown"]
    assert gb is None or {"causes", "total_gap_pct", "left_after_causes"} <= set(gb)


def test_gap_lines_carry_a_price_for_each_month(tmp_path):
    meta, _ = _build_with_mis(tmp_path)
    lines = meta["gap_breakdown"]["left_after_causes"]
    assert lines and all(x["months"] for x in lines)
    m = lines[0]["months"][0]
    assert {"month", "billed_px", "file_px", "gap_pct"} <= set(m) and m["billed_px"] > 0 and m["file_px"] > 0
