from dataclasses import replace
from itertools import product
import random

import pytest

from ch3.revision.history import (
    Clause, Contract, Decision, Edit, Event, Obligation, Patch, Replacement,
    RevisionResult, Snapshot, State, apply_patch, apply_replacement, decide,
    monitor, possible_histories, semantic_key, successors,
)


def clause(id, kind, **fields):
    return Clause(id=id, kind=kind, **fields)


def snap(clauses=(), history=(), state=State()):
    return Snapshot(Contract(0, tuple(Obligation(c) for c in clauses)), state,
                    tuple(history), "obs-current").validate()


def order_snapshot(evidence="confirmed", forward=False):
    a = clause("a", "visit", object="blue", target="green")
    b = clause("b", "visit", object="yellow", target="return")
    events = (Event(1, "place", "blue" if forward else "yellow",
                    "green" if forward else "return", evidence),
              Event(2, "place", "yellow" if forward else "blue",
                    "return" if forward else "green"))
    return snap((a, b), events, State("green", "return"))


def add_order(s):
    c = clause("ab", "before", first="a", second="b")
    return apply_patch(s, Patch(version=0, snapshot=s.digest,
        edits=[Edit(op="add", id="ab", quote="先蓝后黄", clause=c)]), "先蓝后黄")


def test_identical_final_state_different_history():
    good = order_snapshot(forward=True)
    bad = order_snapshot()
    assert good.state == bad.state
    assert decide(good, add_order(good)).status == "ready"
    assert decide(bad, add_order(bad)).status == "conflict"
    assert not RevisionResult(add_order(bad), decide(bad, add_order(bad))).executable


def test_uncertain_event_is_not_confirmed_success_or_irreversible_failure():
    s = order_snapshot("unknown")
    decision = decide(s, add_order(s))
    assert decision.status == "need_observation"
    assert decision.feasible_alternatives == 1
    assert decision.history_alternatives == 2
    assert decision.disputed_events == (1,)
    assert decide(order_snapshot("refuted"), add_order(order_snapshot("refuted"))).status == "ready"


def test_new_visit_is_prospective_and_final_location_is_not_a_visit():
    s = snap(history=(Event(1, "place", "blue", "green"),), state=State("green"))
    c = clause("a", "visit", object="blue", target="green")
    patch = Patch(version=0, snapshot=s.digest,
                  edits=[Edit(op="add", id="a", quote="再放一次", clause=c)])
    revised = apply_patch(s, patch, "再放一次")
    assert revised.obligations[0].since == 1
    d = decide(s, revised)
    assert len(d.plan) == 2
    assert d.plan[0].kind == "pick" and d.plan[1].target == "green"
    assert s.history == (Event(1, "place", "blue", "green"),)


def test_new_prohibition_does_not_rewrite_past():
    s = snap(history=(Event(1, "pick", "blue", None),), state=State("hand", holding="blue"))
    c = clause("f", "forbid", object="blue")
    revised = apply_patch(s, Patch(version=0, snapshot=s.digest,
                         edits=[Edit(op="add", id="f", quote="别动蓝色", clause=c)]), "别动蓝色")
    assert decide(s, revised).status == "ready"  # no empty-hand requirement
    with_empty = Contract(1, (*revised.obligations, Obligation(clause("e", "empty"), 1)))
    assert decide(s, with_empty).status == "conflict"


def test_unedited_obligation_and_anchor_survive_both_interfaces():
    s = order_snapshot(forward=True)
    p = Patch(version=0, snapshot=s.digest, edits=[])
    delta = apply_patch(s, p, "其他照旧")
    full = apply_replacement(s, Replacement(version=0, snapshot=s.digest,
                             clauses=[o.clause for o in s.contract.obligations]))
    assert delta.obligations == s.contract.obligations == full.obligations
    assert semantic_key(delta) == semantic_key(full)


def test_full_translation_can_silently_drop_unedited_goal_but_gold_detects_it():
    s = snap((clause("a", "at", object="blue", target="green"), clause("e", "empty")))
    wrong = apply_replacement(s, Replacement(version=0, snapshot=s.digest, clauses=[]))
    assert decide(s, wrong).status == "ready"
    assert semantic_key(wrong) != semantic_key(s.contract)


def test_stale_response_binds_observation_history_and_task():
    s = snap()
    p = Patch(version=0, snapshot=s.digest, edits=[])
    for changed in (replace(s, observation_id="next"),
                    replace(s, history=(Event(1, "place", "blue", "table"),)),
                    replace(s, contract=Contract(1, ()))):
        with pytest.raises(ValueError, match="stale"):
            apply_patch(changed, p, "")


def test_atomic_dependent_edit_rejects_dangling_order():
    s = order_snapshot()
    s = replace(s, contract=add_order(s))
    p = Patch(version=1, snapshot=s.digest,
              edits=[Edit(op="remove", id="a", quote="取消蓝色")])
    with pytest.raises(ValueError, match="reference"):
        apply_patch(s, p, "取消蓝色")
    assert len(s.contract.obligations) == 3


@pytest.mark.parametrize("mode", ("quote", "duplicate", "absent", "mismatch"))
def test_invalid_transactions(mode):
    s = snap((clause("e", "empty"),))
    edits = [Edit(op="remove", id="e", quote="取消")]
    if mode == "duplicate":
        edits *= 2
    elif mode == "absent":
        edits = [Edit(op="remove", id="z", quote="取消")]
    elif mode == "mismatch":
        edits = [Edit(op="replace", id="e", quote="取消", clause=clause("z", "empty"))]
    with pytest.raises(ValueError):
        apply_patch(s, Patch(version=0, snapshot=s.digest, edits=edits), "别的" if mode == "quote" else "取消")


def test_horizon_and_capacity_are_explicit():
    s = snap((clause("a", "at", object="blue", target="green"),))
    assert decide(s, s.contract, horizon=1).status == "conflict"
    assert decide(s, s.contract, horizon=2).status == "ready"
    c = Contract(1, (*s.contract.obligations,
                    Obligation(clause("b", "at", object="yellow", target="green"))))
    assert decide(s, c).status == "conflict"


def exhaustive(s, contract, horizon):
    # No progress-state pruning: independent bounded enumeration checks BFS pruning.
    histories = tuple(possible_histories(s.history))
    def search(traces):
        frontier = [(s.state, traces)]
        for depth in range(horizon + 1):
            next_frontier = []
            for state, current in frontier:
                checks = [monitor(contract, state, t) for t in current]
                if any(v for v, p in checks):
                    continue
                if all(not p for v, p in checks):
                    return True
                for ns, event in successors(state, s.cursor + depth + 1):
                    next_frontier.append((ns, tuple((*t, event) for t in current)))
            frontier = next_frontier
        return False
    if search(histories):
        return "ready"
    return "need_observation" if any(search((h,)) for h in histories) else "conflict"


@pytest.mark.parametrize("seed", range(50))
def test_solver_pruning_against_exhaustive_enumeration(seed):
    rng = random.Random(seed)
    s = order_snapshot(rng.choice(("confirmed", "unknown", "refuted")), rng.choice((True, False)))
    c = add_order(s)
    extras = [Obligation(clause("f", "at", object="blue", target=rng.choice(("table", "green", "return"))), 2)]
    if rng.random() < .5:
        extras.append(Obligation(clause("no", "forbid", object=rng.choice(("blue", "yellow"))), 2))
    c = Contract(1, (*c.obligations, *extras))
    horizon = rng.randrange(5)
    assert decide(s, c, horizon).status == exhaustive(s, c, horizon)


@pytest.mark.parametrize("bad", (State("hand"), State("green", "green"), State("moon")))
def test_invalid_observed_states_rejected(bad):
    with pytest.raises(ValueError):
        snap(state=bad)


def test_id_independent_scoring_preserves_time_semantics():
    a = Contract(0, (Obligation(clause("x", "visit", object="blue", target="green"), 0),))
    b = Contract(0, (Obligation(clause("y", "visit", object="blue", target="green"), 0),))
    c = Contract(0, (Obligation(clause("y", "visit", object="blue", target="green"), 2),))
    assert semantic_key(a) == semantic_key(b)
    assert semantic_key(a) != semantic_key(c)


@pytest.mark.parametrize("value", (True, 1.5, -1, 13))
def test_nonintegral_or_out_of_range_horizon_rejected(value):
    s = snap()
    with pytest.raises(ValueError):
        decide(s, s.contract, value)


@pytest.mark.parametrize("value", (True, 1.5, -1))
def test_nonintegral_or_negative_logical_clocks_rejected(value):
    with pytest.raises(ValueError):
        snap(history=(Event(value, "place", "blue", "green"),))
    with pytest.raises(ValueError):
        Contract(value, ()).validate()
    with pytest.raises(ValueError):
        Contract(0, (Obligation(clause("e", "empty"), value),)).validate()
