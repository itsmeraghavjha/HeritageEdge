"""The data checks: each one must catch the problem it is named for, and stay quiet on clean data."""
from datetime import date

import pandas as pd
import pytest

from engine import integrity, prepare
import synth


def build(tmp_path, billing=None, offer=None):
    synth.make_offer(tmp_path / "offer.xlsx")
    if offer is not None:
        df = pd.read_excel(tmp_path / "offer.xlsx")
        offer(df).to_excel(tmp_path / "offer.xlsx", index=False)
    (billing if billing is not None else synth.billing_rows()).to_csv(tmp_path / "billing.csv", index=False)
    synth.make_mis(tmp_path / "mis.xlsx")
    meta = prepare.build_dataset(tmp_path / "offer.xlsx", tmp_path / "mis.xlsx", tmp_path / "billing.csv",
                                 synth.MONTHS, out_dir=tmp_path / "out")
    return {c["id"]: c for c in meta["integrity"]["checks"]}, meta


def billing(**changes):
    """The clean billing frame, with `changes(df)` applied to it."""
    df = synth.billing_rows(include_specials=False)
    return changes.get("fn", lambda d: d)(df)


@pytest.fixture(scope="module")
def clean(tmp_path_factory):
    return build(tmp_path_factory.mktemp("clean"), billing=synth.billing_rows(include_specials=False))


def test_clean_data_passes_the_checks_that_should_pass(clean):
    checks, meta = clean
    for cid in ("unit_of_sale", "whole_packs", "price_spread", "duplicates", "months_complete", "offer_units",
                "offer_conflicts", "mrp_by_sku", "missed_lines", "cost_source", "pack_identity"):
        assert checks[cid]["status"] == "pass", (cid, checks[cid]["headline"])
    s = meta["integrity"]["summary"]
    assert sum(s.values()) == len(meta["integrity"]["checks"])


def test_the_waterfall_ties_to_the_final_table(clean):
    checks, _ = clean
    ev = dict(map(tuple, checks["waterfall"]["evidence"]))
    assert "0.0%" in checks["waterfall"]["headline"] or "0.00%" in checks["waterfall"]["headline"]
    assert any(k.startswith("Revenue in the model") for k in ev)


def test_a_unit_that_is_not_litres_or_kg_fails(tmp_path):
    def pieces(d):
        d.loc[d["MaterialCode"] == 7001, "SalesUnit"] = "EA"
        return d
    checks, _ = build(tmp_path, billing=billing(fn=pieces))
    assert checks["unit_of_sale"]["status"] == "fail"


def test_a_weight_pack_billed_in_litres_is_a_density_mismatch(tmp_path):
    def wrong(d):
        d.loc[d["MaterialCode"] == 7002, "SalesUnit"] = "L"      # a 400 GMS pack billed in litres
        return d
    checks, _ = build(tmp_path, billing=billing(fn=wrong))
    c = checks["unit_of_sale"]
    assert c["status"] == "fail"
    assert any("400 GMS pack billed in L" in e[0] for e in c["evidence"])


def test_fractional_packs_are_flagged(tmp_path):
    def slip(d):
        d["Packs"] = d["Packs"].astype(float)
        d.loc[d["MaterialCode"] == 7001, "Packs"] = d["Packs"] * 0.9137      # quantity in the wrong unit
        return d
    checks, _ = build(tmp_path, billing=billing(fn=slip))
    assert checks["whole_packs"]["status"] in ("warn", "fail")


def test_a_pack_text_that_disagrees_with_the_product_name_is_flagged(tmp_path):
    def wrong(d):
        d.loc[d["MaterialCode"] == 7002, "PackSize"] = "500 GMS"
        return d
    checks, _ = build(tmp_path, billing=billing(fn=wrong))
    c = checks["pack_identity"]
    assert c["status"] in ("warn", "fail") and any("name says 0.4, billing says 0.5" in e[1] for e in c["evidence"])


def test_repeated_billing_lines_fail(tmp_path):
    def twice(d):
        return pd.concat([d, d.head(3)], ignore_index=True)
    checks, _ = build(tmp_path, billing=billing(fn=twice))
    assert checks["duplicates"]["status"] == "fail"


def test_returns_with_no_quantity_are_reported(tmp_path):
    def returns(d):
        extra = d.iloc[[0]].copy()
        extra["Packs"], extra["Qty_LtrKg"], extra["InvoiceValue_exTax"] = -400, -200.0, -20000.0
        return pd.concat([d, extra], ignore_index=True)
    checks, _ = build(tmp_path, billing=billing(fn=returns))
    ev = dict(map(tuple, checks["credits"]["evidence"]))
    assert not ev["Negative revenue left out (returns, credit notes)"].startswith("₹0.0 L")


def test_billing_on_a_sku_missing_from_the_offer_file_is_a_missed_line(tmp_path):
    def extra_sku(d):
        extra = d[(d["CustomerID"] == 1001) & (d["MaterialCode"] == 7001)].copy()
        extra["MaterialCode"] = 7099
        extra["InvoiceValue_exTax"] = extra["InvoiceValue_exTax"] * 3
        return pd.concat([d, extra], ignore_index=True)
    checks, _ = build(tmp_path, billing=billing(fn=extra_sku))
    assert checks["missed_lines"]["status"] in ("warn", "fail")


def test_gst_written_as_a_whole_number_fails(tmp_path):
    def whole(d):
        d.loc[d["Sku Code"] == 7001, "GST%"] = 5
        return d
    checks, _ = build(tmp_path, offer=whole)
    assert checks["offer_units"]["status"] == "fail"


def test_two_mrps_for_one_sku_are_flagged(tmp_path):
    def revised(d):
        d.loc[(d["Sku Code"] == 7001) & (d["Customer"] == 1001), "New MRP"] = 55.0
        return d
    checks, _ = build(tmp_path, offer=revised)
    assert checks["mrp_by_sku"]["status"] in ("warn", "fail")


def test_an_mrp_that_differs_only_between_states_is_fine(tmp_path):
    def revised(d):
        d.loc[(d["Sku Code"] == 7001) & (d["Customer"] == 1002), "New MRP"] = 55.0
        return d
    b = synth.billing_rows()
    b = b[~((b["CustomerID"] == 1002) & (b["SalesOfficeID"] == 2001))]      # customer 1002 now bills only in Telangana
    checks, _ = build(tmp_path, billing=b, offer=revised)
    assert checks["mrp_by_sku"]["status"] == "pass"
    assert "differ across states" in checks["mrp_by_sku"]["evidence"][0][0] or "across states" in checks["mrp_by_sku"]["evidence"][0][0]


def test_offer_rows_that_disagree_are_flagged(tmp_path):
    def twice(d):
        extra = d[(d["Customer"] == 1001) & (d["Sku Code"] == 7001)].copy()
        extra["New MRP"] = 60.0
        return pd.concat([d, extra], ignore_index=True)
    checks, _ = build(tmp_path, offer=twice)
    assert checks["offer_conflicts"]["status"] in ("warn", "fail")


def test_dropped_terms_report_the_revenue_they_take_with_them(clean):
    checks, _ = clean
    assert checks["dropped_terms"]["status"] in ("warn", "fail")      # DELTA has no MRP in the made-up offer file


# ---------- checks fed with small hand-made frames ----------
def frames(p_billing, p_mis, rev_billing=3000.0, rev_mis=3000.0):
    keys = {"sku": 1.0, "SalesOfficeID": 2.0, "grp": "Modern Formats"}
    everyone = pd.DataFrame([{**keys, "packs": p_billing, "rev": rev_billing, "in_offer": True}])
    mis = pd.DataFrame([{**keys, "packs": p_mis, "rev": rev_mis, "month": "2026-07-01"}])
    return {"everyone": everyone, "mis": mis, "names": {1.0: "Test SKU"}}


def test_same_revenue_but_different_packs_reads_as_a_unit_slip():
    out = integrity._mis_packs(frames(p_billing=1100, p_mis=1000))
    assert out["status"] in ("warn", "fail")
    assert "unit or pack definition" in out["evidence"][1][1]


def test_packs_and_revenue_moving_together_read_as_a_volume_difference():
    out = integrity._mis_packs(frames(p_billing=1100, p_mis=1000, rev_billing=3300.0))
    assert "different volume" in out["evidence"][1][1]


def test_matching_packs_pass():
    assert integrity._mis_packs(frames(1000, 1000))["status"] == "pass"


def test_cells_missing_on_either_side_are_counted():
    f = frames(1000, 1000)
    f["mis"].loc[0, "SalesOfficeID"] = 9.0                      # the MIS has the cell under another office
    out = integrity._cell_coverage(f)
    assert out["status"] == "fail" and out["evidence"][0][1] == "100.0%" and out["evidence"][1][1] == "100.0%"


def months(values, today=date(2026, 11, 7)):
    base = [f"2026-0{7 + i}-01" for i in range(len(values))]
    return {"base_months": base, "monthly": pd.Series(dict(zip(base, values))), "today": today}


def test_a_much_smaller_base_month_fails():
    assert integrity._months_complete(months([100, 100, 40]))["status"] == "fail"
    assert integrity._months_complete(months([100, 105, 95]))["status"] == "pass"


def test_a_base_month_that_is_still_being_billed_warns():
    out = integrity._months_complete(months([100, 100, 100], today=date(2026, 9, 15)))
    assert out["status"] == "warn"


def test_pack_text_helpers():
    assert integrity.pack_class("500 ML") == "volume" and integrity.pack_class("1 LTR") == "volume"
    assert integrity.pack_class("400 GMS") == "weight" and integrity.pack_class("1 KG") == "weight"
    assert integrity.pack_class("ASSORTED") is None
    assert integrity.name_pack_size("Test curd 400 g", prepare.pack_size) == pytest.approx(0.4)
    assert integrity.name_pack_size("Ghee 1 L tin", prepare.pack_size) == pytest.approx(1.0)
    assert integrity.name_pack_size("Plain label", prepare.pack_size) is None


def test_a_check_that_cannot_run_is_skipped_not_fatal():
    out = integrity._whole_packs({})
    assert out["status"] == "skipped"


# ---------- the offer for the base months ----------
def offer_rows(**cols):
    n = 3
    df = pd.DataFrame({"Customer": [1] * n, "Sku Code": [10, 11, 12], "New MRP": [100.0] * n, "TOT Margin on Invoice": [0.2] * n,
                       "On Invoice offer": [5.0, 0.0, 0.0], "Off Invoice Offer in Value": [0.0, 3.0, 0.0]})
    for k, v in cols.items():
        df[k] = v
    return df


def test_month_text_gives_on_and_off_invoice_offers():
    df = offer_rows(**{"Jul'26 - Month Offer": ["NLC Rs. 70 On Invoice Promo- No Debit", "Rs. 4 Promo - Off Invoice claim", None]})
    out, rep = prepare.monthly_offers(df, ["2026-07-01"])
    assert list(out["on_rs"]) == pytest.approx([10.0, 0, 0])        # list price 80, chain pays 70
    assert list(out["off_rs"]) == pytest.approx([0, 4.0, 0])
    assert rep["source"] == "month columns" and rep["unreadable_cells"] == 0


def test_two_base_months_average_their_offers():
    df = offer_rows(**{"Jul'26 - Month Offer": ["Rs. 2 Promo - Off Invoice claim"] * 3, "Aug'26 - Month Offer": ["Rs. 4 Promo - Off Invoice claim"] * 3})
    out, _ = prepare.monthly_offers(df, ["2026-07-01", "2026-08-01"])
    assert list(out["off_rs"]) == pytest.approx([3.0] * 3)


def test_a_missing_month_column_falls_back_to_the_numeric_columns():
    df = offer_rows(**{"Aug'26 - Month Offer": [None] * 3})
    out, rep = prepare.monthly_offers(df, ["2026-07-01", "2026-08-01"])
    assert rep["source"].startswith("numeric") and rep["missing"] == ["2026-07-01"]
    assert list(out["on_rs"]) == [5.0, 0, 0] and list(out["off_rs"]) == [0, 3.0, 0]


def test_unreadable_month_text_is_counted():
    df = offer_rows(**{"Jul'26 - Month Offer": ["something else", "Rs. 4 Promo - Off Invoice claim", None]})
    _, rep = prepare.monthly_offers(df, ["2026-07-01"])
    assert rep["unreadable_cells"] == 1


def test_the_build_warns_when_a_base_month_has_no_offer_column(tmp_path):
    checks, meta = build(tmp_path)
    assert checks["offer_months"]["status"] == "warn" and meta["offer_months"]["missing"]


def test_the_build_reads_month_columns_when_they_exist(tmp_path):
    def add(d):
        for m in ("Jul'26", "Aug'26", "Sep'26"):
            d[f"{m} - Month Offer"] = None
        return d
    checks, meta = build(tmp_path, offer=add)
    assert checks["offer_months"]["status"] == "pass" and meta["offer_months"]["source"] == "month columns"


def lines(billed_px, **cols):
    d = pd.DataFrame({"sku": [1, 2], "name": ["a", "b"], "packs": [100.0, 100.0], "net_px": [billed_px] * 2, "mrp": [100.0] * 2,
                      "mrp_old": [100.0] * 2, "trade": [0.2] * 2, "gst": [0.0] * 2, "offer_rs": [4.0] * 2,
                      "on_rs": [0.0] * 2, "off_rs": [4.0] * 2})
    for k, v in cols.items():
        d[k] = v
    return d


def test_billing_that_ignores_off_invoice_offers_is_flagged():
    out = integrity._off_invoice({"g": lines(80.0)})        # list price 80, billed 80: the Rs 4 offer is not in the price
    assert out["status"] == "warn" and "claimed later" in out["headline"]


def test_billing_that_deducts_off_invoice_offers_passes():
    assert integrity._off_invoice({"g": lines(76.0)})["status"] == "pass"


def test_billing_that_follows_the_old_mrp_is_flagged():
    d = priced([("2026-07-01", 1, 100.0, 6900.0, 100.0, 90.0, 4.0, 0.0)])     # old MRP: 90 x 0.8 - 4 = 68; new: 76; billed 69
    out = integrity._mrp_revision({"lines": d})
    assert out["status"] == "warn" and "old MRP" in out["evidence"][-1][1]


def test_billing_that_follows_the_new_mrp_passes():
    d = priced([("2026-07-01", 1, 100.0, 7600.0, 100.0, 90.0, 4.0, 0.0)])
    assert integrity._mrp_revision({"lines": d})["status"] == "pass"


# ---------- price gap checks ----------
def priced(rows):
    d = pd.DataFrame(rows, columns=["BillMonth", "sku", "packs", "rev", "mrp", "mrp_old", "on_rs", "off_rs"])
    d["name"], d["family"], d["chain"] = "x", "Milk", "ALPHA"
    d["trade"], d["gst"], d["offer_rs"] = 0.2, 0.0, d["on_rs"] + d["off_rs"]
    return d


def test_a_flat_gap_with_no_offer_is_reported():
    # list price 100 x 0.8 = 80; billed 79.2 (-1%) on lines with no offer
    d = priced([("2026-08-01", 1, 100.0, 7920.0, 100.0, 100.0, 0.0, 0.0), ("2026-08-01", 2, 100.0, 7920.0, 100.0, 100.0, 0.0, 0.0)])
    out = integrity._no_offer_gap({"lines": d})
    assert out["status"] == "warn" and "-1.0%" in out["headline"]


def test_billing_at_the_file_price_with_no_offer_passes():
    d = priced([("2026-08-01", 1, 100.0, 8000.0, 100.0, 100.0, 0.0, 0.0)])
    assert integrity._no_offer_gap({"lines": d})["status"] == "pass"


def test_one_base_month_cannot_show_movement():
    d = priced([("2026-08-01", 1, 100.0, 8000.0, 100.0, 100.0, 0.0, 0.0)])
    assert integrity._gap_by_month({"lines": d})["status"] == "skipped"


def test_a_sku_that_switches_from_old_to_new_mrp_is_flagged():
    rows = [("2026-07-01", 1, 100.0, 7200.0, 100.0, 90.0, 0.0, 0.0),     # July billed at the old MRP (90 x 0.8 = 72)
            ("2026-08-01", 1, 100.0, 8000.0, 100.0, 90.0, 0.0, 0.0)]     # August at the new MRP (100 x 0.8 = 80)
    out = integrity._gap_by_month({"lines": priced(rows)})
    assert out["status"] == "fail" and "old MRP" in out["evidence"][-1][1] and "new MRP" in out["evidence"][-1][1]


def test_steady_prices_in_every_month_pass():
    rows = [(m, 1, 100.0, 8000.0, 100.0, 90.0, 0.0, 0.0) for m in ("2026-07-01", "2026-08-01")]
    assert integrity._gap_by_month({"lines": priced(rows)})["status"] == "pass"


# ---------- the MRP in force, and the offer month by month ----------
def lines_for_mrp(rows):
    d = pd.DataFrame(rows, columns=["sku", "BillMonth", "packs", "rev", "mrp", "mrp_old"])
    d["trade"], d["gst"], d["offer_rs"] = 0.2, 0.0, 0.0
    return d


def test_the_old_mrp_is_used_for_the_month_billing_follows_it():
    d = lines_for_mrp([(1, "2026-07-01", 100.0, 7200.0, 100.0, 90.0),      # billed at 72 = old MRP 90 x 0.8
                       (1, "2026-08-01", 100.0, 8000.0, 100.0, 90.0)])     # billed at 80 = new MRP
    out, rep = prepare.mrp_in_force(d)
    assert list(out["mrp"]) == [90.0, 100.0] and list(out["mrp_new"]) == [100.0, 100.0]
    assert rep["sku_months_priced_old"] == 1 and rep["skus"] == 1


def test_a_small_deduction_does_not_flip_the_mrp():
    d = lines_for_mrp([(1, "2026-07-01", 100.0, 7840.0, 100.0, 90.0)])     # 2% under the new price, far from the old one
    out, rep = prepare.mrp_in_force(d)
    assert list(out["mrp"]) == [100.0] and rep["sku_months_priced_old"] == 0


def test_a_sku_with_one_mrp_is_left_alone():
    d = lines_for_mrp([(1, "2026-07-01", 100.0, 7000.0, 100.0, 100.0)])
    assert list(prepare.mrp_in_force(d)[0]["mrp"]) == [100.0]


def test_each_month_keeps_its_own_offer():
    df = offer_rows(**{"Jul'26 - Month Offer": ["Rs. 2 Promo - Off Invoice claim"] * 3, "Aug'26 - Month Offer": ["Rs. 4 Promo - Off Invoice claim"] * 3})
    out, _ = prepare.monthly_offers(df, ["2026-07-01", "2026-08-01"])
    assert list(out["off_rs|2026-07-01"]) == [2.0] * 3 and list(out["off_rs|2026-08-01"]) == [4.0] * 3


def test_margin_taken_off_the_gst_inclusive_mrp_is_detected():
    d = priced([("2026-08-01", 1, 100.0, 0.0, 100.0, 100.0, 0.0, 0.0)])
    d["gst"] = 0.05
    d["rev"] = 100.0 * 100.0 * (1 / 1.05 - 0.2)             # billing follows B
    out = integrity._trade_gst({"lines": d})
    assert out["status"] == "warn" and "B fits more families" in out["headline"]
    d["rev"] = 100.0 * 100.0 * 0.8 / 1.05                   # billing follows A
    assert integrity._trade_gst({"lines": d})["status"] == "pass"


def test_a_formula_that_is_exact_for_some_families_is_not_replaced_by_one_that_is_only_better_overall():
    d = pd.concat([priced([("2026-08-01", 1, 100.0, 0.0, 100.0, 100.0, 0.0, 0.0)]).assign(family="Exact"),
                   priced([("2026-08-01", 2, 100.0, 0.0, 100.0, 100.0, 0.0, 0.0)]).assign(family="Under", sku=2)])
    d["gst"] = 0.05
    d.loc[d["family"] == "Exact", "rev"] = 100.0 * 100.0 * 0.8 / 1.05          # exactly A
    d.loc[d["family"] == "Under", "rev"] = 100.0 * 100.0 * 0.8 / 1.05 * 0.98   # 2% under A, close to B
    out = integrity._trade_gst({"lines": d})
    assert out["status"] == "pass" and "A is exact" in out["headline"]


def test_the_top_skus_by_rupees_are_listed_for_a_no_offer_gap():
    d = priced([("2026-08-01", 1, 100.0, 7600.0, 100.0, 100.0, 0.0, 0.0), ("2026-08-01", 2, 100.0, 8000.0, 100.0, 100.0, 0.0, 0.0)])
    out = integrity._no_offer_gap({"lines": d, "n": 1})
    assert any(k.startswith("SKU: ") for k, _ in out["evidence"])


def test_a_new_mrp_does_not_go_back_to_the_old_one():
    d = lines_for_mrp([(1, "2026-07-01", 100.0, 8000.0, 100.0, 90.0),      # July follows the new MRP
                       (1, "2026-08-01", 100.0, 7200.0, 100.0, 90.0)])     # August looks like the old one: noise, not a rollback
    out, rep = prepare.mrp_in_force(d)
    assert list(out["mrp"]) == [100.0, 100.0] and rep["sku_months_priced_old"] == 0
