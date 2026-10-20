"""Readiness stays factual while each related collection is traversed once."""
from copy import deepcopy
from datetime import time
import random
from uuid import UUID

import pytest

from app import onboarding_service as service
from test_onboarding import configuration, facts


class Counted(list):
    def __init__(self, values):
        super().__init__(values)
        self.visits = 0
    def __iter__(self):
        for item in super().__iter__():
            self.visits += 1
            yield item


def original_bindings(pros, services, current):
    """The previous nested implementation is retained as a parity oracle."""
    fitting_services, fitting_professionals = set(), set()
    for s in services:
        duration = s.get("duration_min", 0) + s.get("prep_min", 0) + s.get("cleanup_min", 0)
        if duration <= 0 or type(s.get("price_cents")) is not int:
            continue
        for p in pros:
            if not any(str(link["service_id"]) == str(s["id"]) and str(link["professional_id"]) == str(p["id"])
                       for link in current.get("links", [])):
                continue
            for w in current.get("hours", []):
                if str(w["professional_id"]) != str(p["id"]): continue
                blocks = sorted((service._minutes(b["start_time"]), service._minutes(b["end_time"]))
                    for b in current.get("breaks", []) if str(b["working_hour_id"]) == str(w["id"]))
                cursor, end = service._minutes(w["start_time"]), service._minutes(w["end_time"])
                for start, stop in blocks + [(end, end)]:
                    if start - cursor >= duration:
                        fitting_services.add(str(s["id"]))
                        fitting_professionals.add(str(p["id"]))
                    cursor = max(cursor, stop)
    return fitting_services, fitting_professionals


def eligible(current, managed=None, *, staged=True):
    managed = managed or {}
    pros = [p for p in current.get("professionals", [])
        if service._usable(p, "professionals", managed, staged=staged) and p.get("accepts_appointments")]
    services = [s for s in current.get("services", []) if service._usable(s, "services", managed, staged=staged)]
    return pros, services


@pytest.mark.parametrize("count", [2, 200, 281, 1000])
def test_readiness_has_linear_collection_visit_budget_for_real_and_large_profiles(count):
    current = facts(); template_pro = current["professionals"][0]; template_hour = current["hours"][0]
    current["professionals"] = [{**template_pro, "id": UUID(int=100+i)} for i in range(count)]
    sid = current["services"][0]["id"]
    current["links"] = [{"service_id": sid, "professional_id": pro["id"]} for pro in current["professionals"]]
    current["hours"] = [{**template_hour, "id": UUID(int=10000+i), "professional_id": pro["id"]}
        for i, pro in enumerate(current["professionals"])]
    current["breaks"] = [{"working_hour_id": hour["id"], "start_time": time(12), "end_time": time(13)} for hour in current["hours"]]
    for name in ("professionals", "services", "links", "hours", "breaks"):
        current[name] = Counted(current[name])
    assert service.readiness(configuration(), current) == []
    assert {name: current[name].visits for name in ("professionals", "services", "links", "hours", "breaks")} == {
        "professionals": count, "services": 1, "links": count, "hours": count, "breaks": count}


@pytest.mark.parametrize("change", ["no_pro", "no_service", "inactive_pro", "no_appointments", "inactive_service",
    "no_links", "no_hours", "wrong_pro_hours", "wrong_service_link", "no_price", "boolean_price", "zero_duration",
    "buffers_do_not_fit", "exact_fit", "overlap_breaks", "adjacent_breaks", "seconds", "another_day_fits"])
def test_each_binding_rule_matches_previous_algorithm(change):
    current = facts(); pro = current["professionals"][0]; item = current["services"][0]; hour = current["hours"][0]
    if change == "no_pro": current["professionals"] = []
    elif change == "no_service": current["services"] = []
    elif change == "inactive_pro": pro["active"] = False
    elif change == "no_appointments": pro["accepts_appointments"] = False
    elif change == "inactive_service": item["active"] = False
    elif change == "no_links": current["links"] = []
    elif change == "no_hours": current["hours"] = []
    elif change == "wrong_pro_hours": hour["professional_id"] = UUID(int=999)
    elif change == "wrong_service_link": current["links"][0]["service_id"] = UUID(int=999)
    elif change == "no_price": item["price_cents"] = None
    elif change == "boolean_price": item["price_cents"] = True
    elif change == "zero_duration": item.update(duration_min=0, prep_min=0, cleanup_min=0)
    elif change == "buffers_do_not_fit": item.update(duration_min=530, prep_min=5, cleanup_min=10)
    elif change == "exact_fit": item.update(duration_min=525, prep_min=5, cleanup_min=10)
    elif change in ("overlap_breaks", "adjacent_breaks"):
        current["breaks"] = [{"working_hour_id": hour["id"], "start_time": time(10), "end_time": time(13)},
            {"working_hour_id": hour["id"], "start_time": time(12 if change == "overlap_breaks" else 13), "end_time": time(17)}]
        item.update(duration_min=90, prep_min=0, cleanup_min=0)
    elif change == "seconds":
        hour.update(start_time="09:00:30", end_time="10:00:30"); item.update(duration_min=45, prep_min=5, cleanup_min=10)
    elif change == "another_day_fits":
        hour.update(end_time=time(9, 30)); current["hours"].append({**hour, "id": UUID(int=999), "weekday": 3, "end_time": time(18)})
    pros, services = eligible(current)
    expected = original_bindings(pros, services, current)
    assert service._fitting_bindings(pros, services, current) == expected
    codes = [row["code"] for row in service.readiness(configuration(), current)]
    missing = not expected[0] or any(str(s["id"]) not in expected[0] for s in services) or any(str(p["id"]) not in expected[1] for p in pros)
    assert ("scheduling_bindings" in codes) is missing


@pytest.mark.parametrize("staged,requested,expected_fit", [(True, True, True), (True, False, False), (False, True, False)])
def test_draft_requested_active_and_published_inactive_are_not_conflated(staged, requested, expected_fit):
    current = facts(); pro = current["professionals"][0]; item = current["services"][0]
    pro["active"] = item["active"] = False
    managed = {"professionals": {str(pro["id"]): requested}, "services": {str(item["id"]): requested}}
    pros, services = eligible(current, managed, staged=staged)
    assert service._fitting_bindings(pros, services, current) == original_bindings(pros, services, current)
    assert (service.readiness(configuration(), current, managed, staged=staged) == []) is expected_fit


def test_service_and_professional_each_require_their_own_real_binding():
    current = facts(); pro = current["professionals"][0]; item = current["services"][0]
    current["professionals"].append({**pro, "id": UUID(int=200)})
    current["services"].append({**item, "id": UUID(int=201)})
    assert [row["code"] for row in service.readiness(configuration(), current)] == ["scheduling_bindings"]
    current["links"].extend([{"service_id": item["id"], "professional_id": UUID(int=200)},
        {"service_id": UUID(int=201), "professional_id": pro["id"]}])
    assert service.readiness(configuration(), current)  # The second professional still has no shift.
    current["hours"].append({**current["hours"][0], "id": UUID(int=202), "professional_id": UUID(int=200)})
    assert service.readiness(configuration(), current) == []


def test_products_only_does_not_evaluate_unused_scheduling_fields(monkeypatch):
    monkeypatch.setenv("COMMERCIAL_CATALOG_ENABLED", "1")
    current = facts(); current["services"][0]["duration_min"] = None; current["hours"][0]["start_time"] = "not-a-time"
    assert service.readiness(configuration(activity="products"), current) == []


def test_indices_are_not_cached_across_changed_facts():
    current = facts(); original = deepcopy(current)
    assert service.readiness(configuration(), current) == []
    current["breaks"] = [{"working_hour_id": current["hours"][0]["id"], "start_time": time(9), "end_time": time(18)}]
    assert [row["code"] for row in service.readiness(configuration(), current)] == ["scheduling_bindings"]
    assert service.readiness(configuration(), original) == []


def test_mixed_generated_fact_sets_have_exact_binding_parity():
    rng = random.Random(20261018)
    for _ in range(40):
        current = facts(); template = current["professionals"][0]; item = current["services"][0]; hour = current["hours"][0]
        current["professionals"] = [{**template, "id": UUID(int=100+i), "active": rng.choice([True, True, False])} for i in range(6)]
        current["services"] = [{**item, "id": UUID(int=200+i), "duration_min": rng.choice([20, 40, 90, 200]), "active": rng.choice([True, True, False])} for i in range(4)]
        current["links"] = [{"service_id": s["id"], "professional_id": p["id"]} for s in current["services"] for p in current["professionals"] if rng.choice([True, False])]
        current["hours"] = [{**hour, "id": UUID(int=1000+i), "professional_id": p["id"], "end_time": time(rng.choice([10, 13, 18]))} for i, p in enumerate(current["professionals"])]
        current["breaks"] = [{"working_hour_id": h["id"], "start_time": time(10), "end_time": time(12)} for h in current["hours"] if rng.choice([True, False])]
        pros, services = eligible(current)
        assert service._fitting_bindings(pros, services, current) == original_bindings(pros, services, current)
