"""Several built periods at once: each is kept, each person chooses their own, offer files are kept per month."""
import io
import json
import shutil

import pandas as pd
import pytest

from engine import data, datastore, prepare, simulate
from tests.test_kam import client_for, env, post_json, world  # noqa: F401  (fixtures)


def _period(key, month, built_at, scale, mtd_days=None):
    folder = data.period_dir(key)
    folder.mkdir(parents=True, exist_ok=True)
    rows = pd.read_csv(data.SAMPLE_DIR / "rows.csv")
    rows["units"] = rows["units"] * scale
    rows.to_csv(folder / "rows.csv", index=False)
    meta = {"base_months": [month], "built_at": built_at, "rows": len(rows)}
    if mtd_days:
        meta["period"] = {"mtd": True, "days": mtd_days}
    (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


@pytest.fixture
def real_data(monkeypatch):
    """The test setup forces the sample data; these tests need built periods to be read."""
    monkeypatch.delenv("HERITAGEEDGE_USE_SAMPLE", raising=False)


@pytest.fixture
def two_periods(world, real_data):
    _period("2026-10", "2026-10-01", "2026-11-13T07:00:00", 1.0)
    _period("2026-11", "2026-11-01", "2026-11-16T09:00:00", 2.0, mtd_days=13)
    data.reload()
    simulate.refresh()
    yield
    shutil.rmtree(data.periods_dir())
    data.reload()
    simulate.refresh()


def units():
    return sum(r["units"] for r in data.ROWS)


def test_both_periods_are_kept_and_the_newest_build_is_the_default(two_periods):
    keys = [p["key"] for p in data.periods()]
    assert keys == ["2026-11", "2026-10"]                       # newest month first
    assert data.current().key == "2026-11" and [p["default"] for p in data.periods()] == [True, False]


def test_rebuilding_an_older_month_does_not_change_what_people_see_by_default(two_periods):
    _period("2026-10", "2026-10-01", "2026-11-20T09:00:00", 1.0)           # October rebuilt after November
    data.reload()
    assert data.current().key == "2026-11"


def test_choosing_a_period_changes_the_numbers_only_while_chosen(two_periods):
    nov = units()
    token = data.use("2026-10")
    try:
        assert data.current().key == "2026-10" and units() == pytest.approx(nov / 2)
        assert data.days_per_month() == 31.0                    # a whole October
        assert sum(m["units"] for m in simulate.BASE_METRICS) == pytest.approx(nov / 2)
    finally:
        data.release(token)
    assert data.current().key == "2026-11" and data.days_per_month() == 13.0


def test_an_unknown_period_falls_back_to_the_default(two_periods):
    token = data.use("1999-01")
    try:
        assert data.current().key == "2026-11"
    finally:
        data.release(token)


def test_each_person_has_their_own_period(two_periods):
    ceo, boss = client_for("ceo@example.com", "Ceo-pass-12345"), client_for("boss@example.com", "Boss-pass-12345")
    assert post_json(ceo, "/api/period", {"key": "2026-10"}).status_code == 200
    a, b = ceo.get("/api/meta").get_json(), boss.get("/api/meta").get_json()
    assert (a["period_key"], a["days"]) == ("2026-10", 31.0)    # the one who chose
    assert (b["period_key"], b["days"]) == ("2026-11", 13.0)    # nobody else moved


def test_a_period_that_is_not_built_is_refused(two_periods):
    r = post_json(client_for("ceo@example.com", "Ceo-pass-12345"), "/api/period", {"key": "2025-01"})
    assert r.status_code == 400


def test_a_period_needs_a_login(two_periods):
    import app
    r = post_json(app.app.test_client(), "/api/period", {"key": "2026-10"})      # not signed in
    assert r.status_code in (401, 302, 403)


def test_the_sidebar_switcher_appears_only_with_two_periods(world, real_data):
    page = client_for("ceo@example.com", "Ceo-pass-12345").get("/")
    assert b'id="periodPick"' not in page.data                  # only the sample data
    _period("2026-10", "2026-10-01", "2026-11-13T07:00:00", 1.0)
    _period("2026-11", "2026-11-01", "2026-11-16T09:00:00", 2.0, mtd_days=13)
    data.reload()
    try:
        page = client_for("ceo@example.com", "Ceo-pass-12345").get("/")
        assert b'id="periodPick"' in page.data and b"Nov 2026 so far (13 days)" in page.data
    finally:
        shutil.rmtree(data.periods_dir())
        data.reload()


def test_a_single_old_build_is_moved_into_its_period_folder(world, real_data):
    rows = pd.read_csv(data.SAMPLE_DIR / "rows.csv")
    rows.to_csv(data.DATA_DIR / "rows.csv", index=False)
    (data.DATA_DIR / "meta.json").write_text(json.dumps({"base_months": ["2026-09-01"], "built_at": "2026-10-02T08:00:00"}))
    try:
        data.reload()
        assert [p["key"] for p in data.periods()] == ["2026-09"] and not (data.DATA_DIR / "rows.csv").exists()
        assert (data.period_dir("2026-09") / "rows.csv").exists() and data.MODE == "actual"
    finally:
        shutil.rmtree(data.periods_dir())
        data.reload()


def test_period_key_and_labels():
    assert data.period_key(["2026-09-01", "2026-08-01"]) == "2026-08_2026-09"
    assert data.period_label({"base_months": ["2026-10-01"]}) == "Oct 2026"
    assert data.period_label({"base_months": ["2026-07-01", "2026-09-01"]}) == "Jul to Sep 2026 (average)"
    nov = {"base_months": ["2026-11-01"], "period": {"mtd": True, "days": 13}}
    assert data.period_label(nov, short=True) == "Nov 2026 so far (13 days)"


# ---------- offer files, one per month ----------
class Upload:
    def __init__(self, name, frame):
        buf = io.BytesIO()
        frame.to_excel(buf, index=False)
        self.filename, self._bytes = name, buf.getvalue()

    def save(self, target):
        target.write_bytes(self._bytes)


def offer_frame(*months):
    cols = {"Customer": [1], "Sku Code": [1], "New MRP": [10], "TOT Margin on Invoice": [0.1], "On Invoice offer": [0],
            "Off Invoice Offer in Value": [0], "GST%": [0.05]}
    cols.update({f"{m}'26 - Month Offer": [""] for m in months})
    return pd.DataFrame(cols)


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "DATA_DIR", tmp_path)
    return tmp_path


def test_the_months_an_offer_file_covers_are_read_from_its_headers(tmp_path):
    path = tmp_path / "o.xlsx"
    offer_frame("Sep", "Oct").to_excel(path, index=False)
    assert prepare.offer_file_months(path) == ["2026-09-01", "2026-10-01"]
    assert prepare.offer_file_months(tmp_path / "missing.xlsx") == []


def test_offer_files_of_different_months_are_all_kept(uploads):
    datastore.save_upload("offer", Upload("Oct offer.xlsx", offer_frame("Sep", "Oct")))
    datastore.save_upload("offer", Upload("Nov offer.xlsx", offer_frame("Oct", "Nov")))
    assert [o["name"] for o in datastore.offers()] == ["Oct offer.xlsx", "Nov offer.xlsx"]
    assert datastore.uploaded("offer")["name"] == "Nov offer.xlsx"


def test_a_newer_version_of_the_same_file_replaces_the_old_one(uploads):
    datastore.save_upload("offer", Upload("Nov offer.xlsx", offer_frame("Oct", "Nov")))
    datastore.save_upload("offer", Upload("Nov offer v2.xlsx", offer_frame("Oct", "Nov")))
    assert [o["name"] for o in datastore.offers()] == ["Nov offer v2.xlsx"]
    assert len([p for p in (uploads / "uploads").iterdir() if p.name.startswith("offer_")]) == 1


def test_a_build_picks_the_offer_file_that_has_its_month(uploads):
    datastore.save_upload("offer", Upload("Sep file.xlsx", offer_frame("Aug", "Sep")))
    datastore.save_upload("offer", Upload("Oct file.xlsx", offer_frame("Sep", "Oct")))
    datastore.save_upload("offer", Upload("Nov file.xlsx", offer_frame("Oct", "Nov")))
    name = lambda months: next(o["name"] for o in datastore.offers() if o["stored"] == datastore.pick_offer(months).name)
    assert name(["2026-11-01"]) == "Nov file.xlsx"
    assert name(["2026-09-01"]) == "Oct file.xlsx"              # Oct and Nov files hold Oct; the newest that has Sep
    assert name(["2026-08-01"]) == "Sep file.xlsx"
    assert name(["2026-01-01"]) == "Nov file.xlsx"              # nobody has it: the newest, and the build says what is missing


def test_the_mis_workbook_is_still_a_single_file(uploads):
    datastore.save_upload("mis", Upload("a.xlsx", offer_frame("Oct")))
    datastore.save_upload("mis", Upload("b.xlsx", offer_frame("Oct")))
    assert datastore.uploaded("mis")["name"] == "b.xlsx" and len(list((uploads / "uploads").glob("mis.*"))) == 1


def test_an_offer_file_from_before_several_were_kept_still_works(uploads):
    folder = uploads / "uploads"
    folder.mkdir()
    (folder / "offer.xlsx").write_bytes(b"x")
    (folder / "manifest.json").write_text(json.dumps({"offer": {"name": "Old.xlsx", "stored": "offer.xlsx", "size": 1,
                                                                 "uploaded_at": "2026-10-01T00:00:00"}}))
    assert datastore.uploaded("offer")["name"] == "Old.xlsx" and datastore.pick_offer(["2026-10-01"]).name == "offer.xlsx"


def test_an_old_file_that_cannot_be_removed_does_not_stop_the_move(world, real_data, monkeypatch):
    pd.read_csv(data.SAMPLE_DIR / "rows.csv").to_csv(data.DATA_DIR / "rows.csv", index=False)
    (data.DATA_DIR / "meta.json").write_text(json.dumps({"base_months": ["2026-11-01"], "built_at": "2026-11-13T07:00:00"}))
    (data.DATA_DIR / "sku_gap.csv").write_text("sku,gap\n")
    real_unlink = type(data.DATA_DIR).unlink

    def locked(self, *a, **k):                         # Windows: "being used by another process"
        if self.name == "sku_gap.csv":
            raise PermissionError(32, "in use")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(type(data.DATA_DIR), "unlink", locked)
    try:
        data.reload()                                  # must not raise
        assert [p["key"] for p in data.periods()] == ["2026-11"]
        assert (data.period_dir("2026-11") / "sku_gap.csv").exists()
    finally:
        shutil.rmtree(data.periods_dir())
        data.reload()


def test_a_period_says_what_changed_after_it_was_built(uploads):
    datastore.save_upload("mis", Upload("m.xlsx", offer_frame("Oct")))
    mis_time = datastore.uploaded("mis")["uploaded_at"]
    period = {"built_at": "2000-01-01T00:00:00", "months": ["2026-10-01"]}
    reasons = datastore._out_of_date(period, {"pulled_at": "2999-01-01T00:00:00"})
    assert reasons == ["billing was pulled again", "the MIS was replaced"]
    fresh = {"built_at": "2999-12-31T00:00:00", "months": ["2026-10-01"]}
    assert datastore._out_of_date(fresh, {"pulled_at": "2026-11-01T00:00:00"}) == [] and mis_time < fresh["built_at"]


def test_only_an_offer_file_that_covers_the_period_makes_it_out_of_date(uploads):
    datastore.save_upload("offer", Upload("Nov file.xlsx", offer_frame("Oct", "Nov")))
    old = {"built_at": "2000-01-01T00:00:00", "months": ["2026-10-01"]}
    assert datastore._out_of_date(old, None) == ["an offer file was added"]
    other = {"built_at": "2000-01-01T00:00:00", "months": ["2026-03-01"]}      # the file has no March column
    assert datastore._out_of_date(other, None) == []


def test_months_are_filled_in_for_an_offer_file_uploaded_before_they_were_read(uploads):
    folder = uploads / "uploads"
    folder.mkdir()
    offer_frame("Sep", "Oct").to_excel(folder / "offer.xlsx", index=False)
    (folder / "manifest.json").write_text(json.dumps({"offer": {"name": "Old.xlsx", "stored": "offer.xlsx", "size": 1,
                                                                 "uploaded_at": "2026-10-01T00:00:00"}}))
    assert datastore.offers()[0]["months"] == ["2026-09-01", "2026-10-01"]
    assert json.loads((folder / "manifest.json").read_text())["offers"][0]["months"] == ["2026-09-01", "2026-10-01"]
