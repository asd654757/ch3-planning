from dataclasses import replace

import pytest

from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.fragment_domain_adapter import IndependentActionBound
from ch3.revision.fragment_purpose_repair import PurposePolicy, SharedReadyCache
from ch3.revision.fragment_runtime import build_fragment_runtime
from ch3.revision.repair_coordinator import RepairCoordinator


def setup():
    d = build_compact_domain()
    m = d.model
    clauses = (m.Clause(id="goal", kind="at", object="blue", target="green"),
               m.Clause(id="safe", kind="forbid", object="yellow"),
               m.Clause(id="visit", kind="visit", object="red", target="table"))
    snapshot = m.Snapshot(m.Contract(1, tuple(m.Obligation(c) for c in clauses)),
                          m.State(yellow="green"), (), "blocked")
    bank = d.regions.RegionBank.verified([], d.old.Oracle())
    session = d.Session(bank)
    session.freeze()
    lower = IndependentActionBound(d)
    bridge = build_fragment_runtime(d, session, lower)
    runtime = bridge.Runtime(snapshot)
    assert runtime.assess()[0].status == "conflict"
    coordinator = RepairCoordinator(d, runtime, session, lower, SharedReadyCache(d), method="standard")
    policy = PurposePolicy("explicit-fixture", runtime.snapshot.digest, ("blue",))
    return d, session, bridge, runtime, coordinator, policy


def test_proposal_adoption_continues_through_original_dispatch_and_observation_loop():
    d, _, bridge, rt, coordinator, policy = setup()
    before = rt.snapshot
    issued = coordinator.suggest(policy)
    assert rt.snapshot == before and rt.pending
    with pytest.raises(ValueError, match="no executable"):
        rt.issue()
    decision, _ = coordinator.apply(issued)
    assert rt.snapshot.history == before.history and rt.snapshot.state == before.state
    assert rt.snapshot.contract.version == before.contract.version+1
    assert bridge.unchanged_runtime_methods
    attempts = 0
    while decision.plan:
        pre = rt.snapshot
        action = decision.plan[0]
        command = rt.consume(rt.issue())
        assert command.object_id == action.object+"_candidate" and rt.snapshot is pre
        state, observed = next((s, e) for s, e in d.model.successors(pre.state, pre.cursor+1)
            if (e.kind, e.object, e.target) == (action.kind, action.object, action.target))
        rt.observe(state, observation_id=f"o{attempts}", events=(observed,))
        decision, _ = rt.assess(decision.plan[1:])
        assert decision.status == "ready"
        attempts += 1
    assert attempts == 4 and rt.snapshot.state.blue == "return" and rt.snapshot.state.yellow == "green"
    assert d.model.monitor(rt.snapshot.contract, rt.snapshot.state, rt.snapshot.history) == (frozenset(), frozenset())


def test_no_effect_observation_invalidates_previously_issued_repair():
    _, _, _, rt, coordinator, policy = setup()
    issued = coordinator.suggest(policy)
    rt.observe(rt.snapshot.state, observation_id="fresh-no-effect")
    with pytest.raises(ValueError, match="stale"):
        coordinator.apply(issued)


def test_repair_cannot_be_replayed_or_replaced_by_unissued_payload():
    _, _, _, rt, coordinator, policy = setup()
    issued = coordinator.suggest(policy)
    forged = replace(issued, proposal=replace(issued.proposal, plan=()))
    with pytest.raises(ValueError, match="unissued"):
        coordinator.apply(forged)
    coordinator.apply(issued)
    with pytest.raises(ValueError, match="already adopted"):
        coordinator.apply(issued)
    assert rt.snapshot.contract.version == 2


def test_no_permission_leaves_conflict_pending_and_cannot_dispatch():
    _, _, _, rt, coordinator, policy = setup()
    issued = coordinator.suggest(replace(policy, swappable_objects=()))
    assert issued.proposal.status == "no_permitted_substitution" and rt.pending
    with pytest.raises(ValueError):
        coordinator.apply(issued)
    with pytest.raises(ValueError):
        rt.issue()


def test_need_observation_cannot_be_bypassed_as_a_semantic_repair():
    d, _, _, rt, coordinator, _ = setup()
    m = d.model
    task = m.Contract(2, (m.Obligation(m.Clause(id="v", kind="visit", object="blue", target="green")),
                         m.Obligation(m.Clause(id="f", kind="forbid", object="blue"), 2)))
    rt.snapshot = m.Snapshot(task, m.State(), (m.Event(2, "place", "blue", "green", "unknown"),), "uncertain")
    assert rt.assess()[0].status == "need_observation"
    with pytest.raises(ValueError, match="uncertainty"):
        coordinator.suggest(PurposePolicy("explicit", rt.snapshot.digest, ("blue",)))


def test_frozen_session_and_horizon_are_checked_before_adoption():
    _, session, _, rt, coordinator, policy = setup()
    issued = coordinator.suggest(policy)
    session.learning = True
    with pytest.raises(ValueError, match="frozen"):
        coordinator.apply(issued)
    session.learning = False
    rt.horizon = 12
    with pytest.raises(ValueError, match="frozen"):
        coordinator.apply(issued)
