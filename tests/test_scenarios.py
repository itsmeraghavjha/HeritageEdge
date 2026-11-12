"""Saved scenarios: who sees which, who may change them, the numbers on the list and the cleaning of what is saved.
Uses the sample data and the same people as the KAM tests; never real names."""
import pytest

import app as app_module
from engine import activity, data, scenarios, simulate, users
from tests.test_kam import client_for, env, post_json, world  # noqa: F401  (fixtures)

LEVERS = {"promo_add_pp": 3, "volume_change_pct": 10}


def person(email):
    return users.get_by_email(email)


def names_seen_by(c):
    return {s["name"] for s in c.get("/api/scenarios").get_json()["scenarios"]}


@pytest.fixture
def saved(world):
    """Asha and Ravi (KAMs) and the CEO each save one scenario."""
    for email, name in (("asha@example.com", "Asha promo"), ("ravi@example.com", "Ravi promo"), ("ceo@example.com", "CEO view")):
        scenarios.save(person(email), name, LEVERS, {})
    return world


# ---------- who sees what ----------
def test_a_kam_sees_only_their_own_scenarios(saved):
    assert names_seen_by(client_for("asha@example.com", "Own-pass-12345")) == {"Asha promo"}
    assert names_seen_by(client_for("ravi@example.com", "Own-pass-12345")) == {"Ravi promo"}


def test_leadership_and_admin_see_everyones(saved):
    everything = {"Asha promo", "Ravi promo", "CEO view"}
    assert names_seen_by(client_for("ceo@example.com", "Ceo-pass-12345")) == everything
    assert names_seen_by(client_for("boss@example.com", "Boss-pass-12345")) == everything


def test_a_kam_cannot_open_or_change_another_persons_scenario(saved):
    ravi = client_for("ravi@example.com", "Own-pass-12345")
    other = scenarios.listing(person("asha@example.com"))[0]["id"]
    assert ravi.get(f"/api/scenarios/{other}").status_code == 404
    assert ravi.delete(f"/api/scenarios/{other}").status_code == 400
    assert post_json(ravi, f"/api/scenarios/{other}/rename", {"name": "mine now"}).status_code == 400
    assert post_json(ravi, f"/api/scenarios/{other}/duplicate", {}).status_code == 400
    assert len(scenarios.listing(person("asha@example.com"))) == 1


def test_leadership_can_open_and_copy_but_not_delete_a_kams_scenario(saved):
    ceo = client_for("ceo@example.com", "Ceo-pass-12345")
    other = next(s["id"] for s in scenarios.listing(person("ceo@example.com")) if s["name"] == "Asha promo")
    assert ceo.get(f"/api/scenarios/{other}").status_code == 200
    assert ceo.delete(f"/api/scenarios/{other}").status_code == 400
    copy_id = post_json(ceo, f"/api/scenarios/{other}/duplicate", {}).get_json()["id"]
    assert scenarios.get(person("ceo@example.com"), copy_id)["name"] == "Asha promo (copy)"
    assert not scenarios.get(person("asha@example.com"), copy_id)         # the copy belongs to the CEO


def test_an_admin_can_delete_anyones_scenario(saved):
    boss = client_for("boss@example.com", "Boss-pass-12345")
    other = scenarios.listing(person("asha@example.com"))[0]["id"]
    assert boss.delete(f"/api/scenarios/{other}").status_code == 200
    assert scenarios.listing(person("asha@example.com")) == []


def test_the_page_needs_a_login(saved):
    assert app_module.app.test_client().get("/scenarios").status_code == 302


# ---------- saving ----------
def test_saving_the_same_name_updates_and_the_id_keeps_the_scenario(saved):
    ceo = person("ceo@example.com")
    first = scenarios.save(ceo, "CEO view", {"promo_add_pp": 5}, {})
    assert first == scenarios.save(ceo, "ceo view", {"promo_add_pp": 7}, {})          # same name, any case
    assert scenarios.get(ceo, first)["levers"]["promo_add_pp"] == 7
    scenarios.save(ceo, "Renamed", {"promo_add_pp": 7}, {}, scenario_id=first)        # by id, with a new name
    assert scenarios.get(ceo, first)["name"] == "Renamed"
    assert len([s for s in scenarios.listing(ceo) if s["mine"]]) == 1


def test_saving_over_someone_elses_scenario_makes_your_own_copy(saved):
    ceo = person("ceo@example.com")
    other = next(s["id"] for s in scenarios.listing(ceo) if s["name"] == "Asha promo")
    mine = scenarios.save(ceo, "Asha promo", {"promo_add_pp": 9}, {}, scenario_id=other)
    assert mine != other
    assert scenarios.get(person("asha@example.com"), other)["levers"]["promo_add_pp"] == 3     # untouched


def test_a_copy_never_replaces_a_scenario_with_the_same_name(world):
    ceo = person("ceo@example.com")
    a = scenarios.save(ceo, "Plan", {"promo_add_pp": 1}, {})
    b = scenarios.save(ceo, "Plan", {"promo_add_pp": 2}, {}, copy=True)
    assert a != b and scenarios.get(ceo, b)["name"] == "Plan (2)"
    assert scenarios.get(ceo, a)["levers"]["promo_add_pp"] == 1


def test_what_is_saved_is_cleaned(world):
    ceo = person("ceo@example.com")
    raw = {"promo_add_pp": 999, "bogus": 1, "adjust": {"sku": {"S1": {"promo_add_pp": 2, "bogus": 5}}, "nope": {"x": {"promo_add_pp": 1}}}}
    sid = scenarios.save(ceo, "  Spaced   name ", raw, {"chain": ["Sample Chain A"], "not_a_dimension": ["x"]})
    got = scenarios.get(ceo, sid)
    assert got["name"] == "Spaced name"
    assert got["levers"]["promo_add_pp"] == simulate.LIMITS["promo_add_pp"][1] and "bogus" not in got["levers"]
    assert got["levers"]["adjust"] == {"sku": {"S1": {"promo_add_pp": 2.0}}}
    assert got["scope"] == {"chain": ["Sample Chain A"]}


def test_names_must_be_given_and_not_too_long(world):
    ceo = person("ceo@example.com")
    for bad in ("", "   ", "x" * 81):
        with pytest.raises(scenarios.ScenarioError):
            scenarios.save(ceo, bad, {}, {})


def test_rename_refuses_a_name_the_person_already_uses(world):
    ceo = person("ceo@example.com")
    scenarios.save(ceo, "One", {}, {})
    two = scenarios.save(ceo, "Two", {}, {})
    with pytest.raises(scenarios.ScenarioError):
        scenarios.rename(ceo, two, "one")


def test_import_from_the_browser_adds_scenarios_and_skips_bad_ones(world):
    ceo = client_for("ceo@example.com", "Ceo-pass-12345")
    out = post_json(ceo, "/api/scenarios/import", {"items": [
        {"name": "Old one", "levers": {"promo_add_pp": 2}, "scope": {}, "adjust": {"sku": {"S1": {"volume_change_pct": 5}}}},
        {"name": "", "levers": {}}, "not a dict"]}).get_json()
    assert out == {"added": 1, "skipped": 2}
    kept = scenarios.listing(person("ceo@example.com"))[0]
    assert kept["levers"]["adjust"] == {"sku": {"S1": {"volume_change_pct": 5.0}}}


# ---------- the numbers on the list ----------
def test_the_headline_matches_the_full_simulation(world):
    full = simulate.simulate(LEVERS, {}, None)
    head = simulate.headline(LEVERS, {}, None)
    for side in ("base", "sim"):
        for k in ("net", "cm", "nm", "litres", "kg", "units"):
            assert head[side][k] == pytest.approx(full[side][k])


def test_a_kams_list_stops_at_contribution_and_uses_their_rows(saved):
    result = client_for("asha@example.com", "Own-pass-12345").get("/api/scenarios").get_json()["scenarios"][0]["result"]
    assert "nm" not in result and "d_nm" not in result and "cm" in result
    assert result["rows"] < len(data.ROWS)                                 # only the rows she may see
    lead = client_for("ceo@example.com", "Ceo-pass-12345").get("/api/scenarios").get_json()["scenarios"][0]["result"]
    assert lead["rows"] == len(data.ROWS) and "nm" in lead


def test_a_scenario_that_no_longer_fits_the_data_does_not_break_the_list(saved, monkeypatch):
    def boom(*a, **k):
        raise KeyError("gone")
    monkeypatch.setattr(simulate, "headline", boom)
    res = client_for("ceo@example.com", "Ceo-pass-12345").get("/api/scenarios").get_json()
    assert len(res["scenarios"]) == 3 and all(s["result"] is None for s in res["scenarios"])


def test_saving_and_deleting_are_logged_without_names_or_numbers(world):
    ceo = client_for("ceo@example.com", "Ceo-pass-12345")
    sid = post_json(ceo, "/api/scenarios", {"name": "Secret chain plan", "levers": LEVERS, "scope": {}}).get_json()["id"]
    ceo.delete(f"/api/scenarios/{sid}")
    rows, _ = activity.recent(50)
    text = " ".join(f"{r['action']} {r['detail']}" for r in rows)
    assert "scenario_saved" in text and "scenario_deleted" in text and "Secret chain plan" not in text
