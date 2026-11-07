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
