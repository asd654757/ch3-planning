from dataclasses import replace

import pytest

from ch3.context_memory.cases import make_cases
from ch3.revision.fragments import (Atom, Oracle, Pattern, VerifiedBank, certify,
    contract, eligible_atoms, extract, lift, location_atoms, states, structural_conflict)
from ch3.revision.history import Clause, Contract, Event, Obligation, Snapshot, State, decide


def snap(atoms, state=State(), history=()):
    return Snapshot(contract(atoms), state, history, "test-fragment").validate()


def capacity():
    return Pattern((Atom("at", "blue", "green"), Atom("at", "yellow", "green")))


def test_state_enumeration_complete_and_legal():
    assert len(states()) == len(set(states())) == 13
    assert all(s.validate() == s for s in states())


def test_extracts_not_hand_authors_capacity_core():
    source = make_cases("validation")[19]["snapshot"]
    oracle = Oracle()
    pattern = extract(source, oracle)
    assert set(pattern.atoms) == set(capacity().atoms)
    assert oracle.calls > 0
    certificate = certify(lift(pattern), oracle)
    assert len(certificate["all_bindings"]) == 4
    assert all(w["plan"] for w in certificate["deletion_witnesses_first_binding"])


def test_protected_blocker_core_drops_unnecessary_hand_and_goal():
    source = make_cases("validation")[15]["snapshot"]
    p = extract(source, Oracle())
    assert set(p.atoms) == {Atom("loc", "yellow", "return"), Atom("forbid", "yellow"),
                           Atom("visit", "blue", "return")}
    assert certify(lift(p), Oracle())["inclusion_minimal"]


@pytest.mark.parametrize("index", [2, 7, 9, 14, 17, 18])
def test_failure_trigger_does_not_establish_task_conflict(index):
    # Includes feasible tasks, evidence uncertainty and historical-only conflict.
    assert extract(make_cases("validation")[index]["snapshot"], Oracle()) is None


def test_lifting_is_invariant_to_names_and_clause_ids():
    other = Pattern((Atom("at", "yellow", "return"), Atom("at", "blue", "return")))
    assert lift(other) == lift(capacity())


def test_finite_certificate_catches_wrong_generalization():
    # A single at requirement is possible: an example failure cannot ban it.
    with pytest.raises(ValueError, match="counterexample"):
        VerifiedBank([Pattern((Atom("at", "blue", "green"),))], Oracle())


def test_deletion_witness_detects_nonminimal_redundancy():
    with pytest.raises(ValueError, match="not deletion-minimal"):
        certify(Pattern((*capacity().atoms, Atom("empty"))), Oracle())


@pytest.mark.parametrize("target", ["green", "return"])
def test_typed_region_transfer_and_unlimited_table_counterexample(target):
    bank = VerifiedBank([lift(capacity())], Oracle())
    assert bank.match(snap([Atom("at", "yellow", target), Atom("at", "blue", target)]))
    assert bank.match(snap([Atom("at", "blue", "table"), Atom("at", "yellow", "table")])) is None


def test_horizon_scope_cannot_be_silently_extended():
    bank = VerifiedBank([lift(capacity())], Oracle())
    snapshot = snap(capacity().atoms)
    assert bank.match(snapshot, horizon=4)
    assert bank.match(snapshot, horizon=12) is None
    with pytest.raises(ValueError):
        bank.match(snapshot, horizon=True)


def test_cannot_use_location_instead_of_historical_fulfillment():
    source = make_cases("validation")[15]["snapshot"]
    bank = VerifiedBank([lift(extract(source, Oracle()))], Oracle())
    assert bank.match(source)
    visit = next(o for o in source.contract.obligations if o.clause.id == "waypoint")
    # A visit before the protection activation already fulfilled this waypoint.
    history = (Event(1, "place", "blue", "return"), Event(2, "pick", "blue", None),
               Event(3, "place", "yellow", "return"))
    task = Contract(0, (replace(visit, since=0), Obligation(Clause(id="safe", kind="forbid", object="yellow"), 3)))
    complete = Snapshot(task, State("table", "return"), history, "fulfilled").validate()
    assert not any(a.kind == "visit" for a in eligible_atoms(complete))
    assert bank.match(complete) is None


@pytest.mark.parametrize("evidence,expected_match", [("confirmed", False), ("unknown", False), ("refuted", True)])
def test_visit_guards_respect_unknown_and_refuted_evidence(evidence, expected_match):
    p = Pattern((Atom("forbid", "yellow"), Atom("loc", "yellow", "return"), Atom("visit", "blue", "return")))
    bank = VerifiedBank([lift(p)], Oracle())
    event = Event(1, "place", "blue", "return", evidence=evidence)
    snapshot = snap([Atom("forbid", "yellow"), Atom("visit", "blue", "return")],
                    State("table", "return"), (event,))
    assert bool(bank.match(snapshot)) == expected_match


def test_event_at_activation_does_not_fulfill_visit():
    task = Contract(0, (Obligation(Clause(id="v", kind="visit", object="blue", target="return"), 1),))
    s = Snapshot(task, State(), (Event(1, "place", "blue", "return"),), "anchor").validate()
    assert Atom("visit", "blue", "return") in eligible_atoms(s)


def test_releasing_blocker_disables_guard_without_retraining():
    source = make_cases("validation")[15]["snapshot"]
    bank = VerifiedBank([lift(extract(source, Oracle()))], Oracle())
    task = Contract(0, tuple(o for o in source.contract.obligations if o.clause.kind != "forbid"))
    changed = replace(source, contract=task)
    assert bank.match(changed) is None
    assert decide(changed, task).status == "ready"


def test_injective_roles_do_not_create_false_same_object_conflict():
    bank = VerifiedBank([lift(capacity())], Oracle())
    assert bank.match(snap([Atom("at", "blue", "green")])) is None


def test_invalid_persisted_pattern_is_recertified_and_rejected():
    wrong = lift(capacity())
    used = {wrong.atoms[0].object, wrong.atoms[0].target}
    with pytest.raises(ValueError, match="counterexample"):
        VerifiedBank([replace(wrong, atoms=wrong.atoms[:1],
                             variables=tuple(v for v in wrong.variables if v[0] in used))], Oracle())


def test_no_solver_calls_during_online_match():
    oracle = Oracle()
    bank = VerifiedBank([lift(capacity())], oracle)
    calls = oracle.calls
    assert bank.match(snap(capacity().atoms))
    assert bank.match(snap([Atom("empty")])) is None
    assert oracle.calls == calls


def test_changed_semantics_require_recertification(monkeypatch):
    bank = VerifiedBank([lift(capacity())], Oracle())
    monkeypatch.setattr("ch3.revision.fragments.semantics_fingerprint", lambda: "changed")
    with pytest.raises(ValueError, match="re-certification"):
        bank.match(snap(capacity().atoms))


@pytest.mark.parametrize("case", make_cases("development")+make_cases("validation"), ids=lambda c: c["id"])
def test_strong_handwritten_baseline_never_falsely_rejects_existing_case(case):
    if structural_conflict(case["snapshot"]):
        assert decide(case["snapshot"], case["snapshot"].contract).status == "conflict"


def test_unregistered_predicates_rejected():
    with pytest.raises(ValueError, match="unsupported"):
        certify(Pattern((Atom("collides", "blue", "green"),)), Oracle())


def test_variables_must_have_correct_sorts():
    with pytest.raises(ValueError, match="wrong sort"):
        certify(Pattern((Atom("at", "?x", "green"),), (("?x", "unit_region"),)), Oracle())
