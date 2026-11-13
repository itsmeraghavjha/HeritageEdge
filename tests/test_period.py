"""Month to date and costs from another month: the Data route checks and what the screens are told."""
from engine import data
from tests.test_kam import client_for, env, post_json, world  # noqa: F401  (fixtures)


def admin():
    return client_for("boss@example.com", "Boss-pass-12345")


def test_the_build_route_refuses_a_bad_number_of_days(world):
    r = post_json(admin(), "/api/data/build", {"months": ["2026-11-01"], "mtd_days": 45})
    assert r.status_code == 400 and "between 1 and 30" in r.get_json()["error"]


def test_the_build_route_refuses_month_to_date_with_several_months(world):
    r = post_json(admin(), "/api/data/build", {"months": ["2026-10-01", "2026-11-01"], "mtd_days": 11})
    assert r.status_code == 400 and "one base month" in r.get_json()["error"]


def test_the_build_route_refuses_a_cost_month_that_is_not_a_month(world):
    r = post_json(admin(), "/api/data/build", {"months": ["2026-11-01"], "cost_month": "October"})
    assert r.status_code == 400


def test_days_per_day_figures_use_the_days_of_billing_in_a_month_to_date(monkeypatch):
    monkeypatch.setattr(data, "META", {"base_months": ["2026-11-01"], "period": {"mtd": True, "days": 11}})
    assert data.days_per_month() == 11.0
    monkeypatch.setattr(data, "META", {"base_months": ["2026-11-01"], "period": {"mtd": False, "days": None}})
    assert data.days_per_month() == 30.0                   # a whole November


def test_the_screens_are_told_the_period_and_where_costs_came_from(world, monkeypatch):
    period = {"mtd": True, "days": 11, "label": "Nov 2026 month to date, 1 to 11 Nov", "short": "Nov MTD"}
    monkeypatch.setattr(data, "META", {"base_months": ["2026-11-01"], "period": period, "costs_from": "Oct 2026"})
    for email, pw in (("ceo@example.com", "Ceo-pass-12345"), ("asha@example.com", "Own-pass-12345")):
        meta = client_for(email, pw).get("/api/meta").get_json()
        assert meta["period"] == period and meta["costs_from"] == "Oct 2026" and meta["days"] == 11.0
