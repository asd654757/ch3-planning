from dataclasses import replace

import pytest

from ch3.revision.fragments import Atom, Oracle, contract
from ch3.revision.fragment_regions import RegionBank, canonical, expert_patterns
from ch3.revision.fragment_session import FragmentSession, exact_key
from ch3.revision.history import Clause, Contract, Event, Obligation, Snapshot, State, monitor, successors


def snapshot(atoms, state=State()):
    return Snapshot(contract(atoms), state, (), "session-test").validate()


def replay(snapshot, plan):
    state = snapshot.state
    for event in plan:
        state = next(ns for ns, e in successors(state, event.seq) if e == event)
    assert monitor(snapshot.contract, state, snapshot.history + plan) == (frozenset(), frozenset())


def empty_session(learn=False):
    return FragmentSession(RegionBank.verified([], Oracle()), learn=learn)


def test_ready_cache_preserves_executable_plan_and_shared_closure():
    session = empty_session()
    source = snapshot([Atom("at", "blue", "green")])
    first = session.resolve(source)
    equivalent = snapshot([Atom("at", "blue", "green"), Atom("visit", "blue", "green")])
    second = session.resolve(equivalent)
    assert first.origin == "backend"
    assert second.origin == "exact_cache" and session.backend_calls == 1
    replay(equivalent, second.plan)


def test_conflict_cache_does_not_trigger_duplicate_learning():
    session = empty_session()
    request = snapshot([Atom("at", "blue", "green"), Atom("at", "blue", "return")])
    assert session.resolve(request).status == "conflict"
    assert session.resolve(request).origin == "exact_cache"
    assert session.backend_calls == 1 and not session.patterns


def test_failure_core_is_learned_then_transfers_to_different_state():
    session = empty_session(learn=True)
    atoms = [Atom("at", "blue", "green"), Atom("at", "yellow", "green")]
    first = session.resolve(snapshot(atoms))
    second = session.resolve(snapshot(atoms, State("return", "table")))
    assert first.new_pattern and len(session.patterns) == 1
    assert second.origin == "certified_core" and second.status == "conflict"
    assert session.backend_calls == 1


def test_freeze_prevents_both_cache_growth_and_new_patterns():
    session = empty_session(learn=True)
    session.freeze()
    request = snapshot([Atom("at", "blue", "green"), Atom("at", "blue", "return")])
    assert session.resolve(request).status == "conflict"
    assert session.resolve(request).origin == "backend"
    assert session.backend_calls == 2 and not session.memo and not session.patterns


def test_history_and_order_are_excluded_from_exact_cache():
    task = Contract(0, (
        Obligation(Clause(id="a", kind="visit", object="blue", target="green")),
        Obligation(Clause(id="b", kind="visit", object="yellow", target="return")),
        Obligation(Clause(id="o", kind="before", first="a", second="b"))))
    assert exact_key(Snapshot(task, State(), (), "order")) is None
    assert exact_key(replace(snapshot([]), history=(Event(1, "pick", "blue", None),))) is None


def test_ready_requests_never_create_core():
    session = empty_session(learn=True)
    result = session.resolve(snapshot([Atom("visit", "blue", "green")]))
    assert result.status == "ready" and not session.patterns and session.oracle.calls == 0


def test_stale_semantics_invalidates_cache_before_reuse(monkeypatch):
    from ch3.revision import history
    session = empty_session()
    request = snapshot([Atom("at", "blue", "green")])
    session.resolve(request)
    monkeypatch.setattr(history, "TARGETS", ("table", "green", "return", "new"))
    with pytest.raises(ValueError, match="recertify"):
        session.resolve(request)


def test_known_rule_can_pass_feasible_boundary_without_rejection():
    rule = canonical([Atom("at", "blue", "green"), Atom("visit", "yellow", "green")])
    session = FragmentSession(RegionBank.verified([*expert_patterns(), rule], Oracle()), learn=True)
    request = snapshot([Atom("at", "blue", "green"), Atom("visit", "yellow", "green")],
                       State("green", "table"))
    result = session.resolve(request)
    assert result.status == "ready" and not session.patterns
    replay(request, result.plan)
