from copy import deepcopy
from dataclasses import replace
import json

import pytest

from ch3.revision import history as model
from ch3.revision.fragments import Atom, Oracle, contract, states
from ch3.revision.fragment_regions import RegionBank, canonical, expert_patterns, features
from ch3.revision.history import Event, Snapshot, State, decide


def snapshot(atoms, state=State(), history=()):
    return Snapshot(contract(atoms), state, history, "region-test").validate()


@pytest.fixture(scope="module")
def bank():
    return RegionBank.verified([canonical([Atom("at", "blue", "return"), Atom("forbid", "blue")])], Oracle())


@pytest.mark.parametrize("state", states())
def test_maximal_guard_covers_exactly_all_bad_core_states(bank, state):
    s = snapshot([Atom("at", "blue", "return"), Atom("forbid", "blue")], state)
    assert bank.match(s) == (decide(s, s.contract).status == "conflict")


@pytest.mark.parametrize("target", ["table", "green", "return"])
def test_target_sort_relaxation_checked_per_binding(bank, target):
    s = snapshot([Atom("at", "yellow", target), Atom("forbid", "yellow")])
    assert bank.match(s) == (target != "table")


def test_capacity_core_does_not_ban_two_objects_on_table():
    b = RegionBank.verified([canonical([Atom("at", "blue", "green"), Atom("at", "yellow", "green")])], Oracle())
    assert not b.match(snapshot([Atom("at", "blue", "table"), Atom("at", "yellow", "table")]))


def test_unsatisfied_terminal_goal_implies_needed_place_for_all_arms():
    b = RegionBank.verified([canonical([Atom("visit", "blue", "return"), Atom("forbid", "blue")])], Oracle())
    assert b.match(snapshot([Atom("at", "yellow", "green"), Atom("forbid", "yellow")]))
    assert not b.match(snapshot([Atom("at", "yellow", "table"), Atom("forbid", "yellow")]))


def test_same_core_generalizes_blocking_location_to_protected_held_object():
    b = RegionBank.verified([canonical([Atom("visit", "blue", "return"), Atom("forbid", "yellow")])], Oracle())
    assert b.match(snapshot([Atom("visit", "blue", "table"), Atom("forbid", "yellow")], State("table", "hand", "yellow")))


@pytest.mark.parametrize("evidence,hit", [("confirmed", False), ("unknown", False), ("refuted", True)])
def test_history_uncertainty_keeps_conservative_future_visit_guard(evidence, hit):
    b = RegionBank.verified([canonical([Atom("visit", "blue", "return"), Atom("forbid", "yellow")])], Oracle())
    s = snapshot([Atom("visit", "blue", "return"), Atom("forbid", "yellow")],
                 State("table", "return"), (Event(1, "place", "blue", "return", evidence),))
    assert b.match(s) == hit


def test_tampered_serialized_state_mask_is_recomputed_and_rejected(bank):
    certs = deepcopy(bank.certificates)
    certs[0]["bindings"][0]["bad_state_mask"] ^= 1
    with pytest.raises(ValueError, match="untrusted certificate"):
        RegionBank(certs)


def test_changed_executing_planner_requires_recertification(bank, monkeypatch):
    monkeypatch.setattr(model, "monitor", lambda *args: (frozenset(), frozenset()))
    with pytest.raises(ValueError, match="recertify"):
        bank.match(snapshot([Atom("empty")]))


def test_invalid_request_horizon_cannot_use_guard(bank):
    s = snapshot([Atom("at", "blue", "return"), Atom("forbid", "blue")])
    assert not bank.match(s, 12)
    assert not bank.match(s, True)
    assert bank.match(s, 0)


def test_loaded_honest_bank_recertifies_and_gives_support(bank):
    loaded = RegionBank(json.loads(json.dumps(bank.certificates)))
    s = snapshot([Atom("at", "yellow", "green"), Atom("forbid", "yellow")])
    assert loaded.match(s)
    assert loaded.evidence(s)["support"]


def test_no_search_during_matching(bank):
    oracle = Oracle()
    b = RegionBank.verified([canonical([Atom("at", "blue", "return"), Atom("forbid", "blue")])], oracle)
    calls = oracle.calls
    assert b.match(snapshot([Atom("at", "blue", "return"), Atom("forbid", "blue")]))
    assert oracle.calls == calls


def test_combining_banks_preserves_certified_guards(bank):
    expert = RegionBank.verified(expert_patterns(), Oracle())
    both = RegionBank.combine(expert, bank)
    s = snapshot([Atom("at", "blue", "green"), Atom("at", "yellow", "green")])
    assert expert.match(s) and both.match(s)
