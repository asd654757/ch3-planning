from dataclasses import replace

import pytest

from ch3.revision import runtime as original_runtime
from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.fragment_domain_adapter import IndependentActionBound
from ch3.revision.fragment_runtime import build_fragment_runtime
from ch3.revision.fragments import Atom


def setup(atoms=(), patterns=(), *, state=None, history=()):
    d = build_compact_domain()
    bank = d.regions.RegionBank.verified([d.regions.canonical(p) for p in patterns], d.old.Oracle())
    session = d.Session(bank)
    session.freeze()
    bridge = build_fragment_runtime(d, session, IndependentActionBound(d))
    snapshot = d.model.Snapshot(d.old.contract(atoms), state or d.model.State(), history, "supported-test").validate()
    return d, session, bridge, bridge.Runtime(snapshot)


def test_internal_certificate_avoids_full_backend_and_suspends():
    atoms = (Atom("at", "blue", "green"), Atom("at", "yellow", "green"))
    d, session, bridge, rt = setup(atoms, (atoms,))
    result, _ = rt.assess()
    assert result.status == "conflict" and not result.plan
    assert session.backend_calls == 0
    assert bridge.records[-1]["origin"] == "certified_core"
    assert bridge.records[-1]["proof"]["support"]
    assert result == d.model.decide(rt.snapshot, rt.snapshot.contract, 8)
    with pytest.raises(ValueError):
        rt.issue()


def test_external_false_conflict_still_requires_original_validation():
    d, session, bridge, rt = setup()
    new = replace(d.old.contract([Atom("at", "red", "green")]), version=1)
    false = d.model.Decision("conflict", (), 1, 0, 8)
    with pytest.raises(ValueError, match="externally supplied"):
        rt.accept(new, expected_digest=rt.snapshot.digest, expected_generation=0, supplied_decision=false)
    assert rt.snapshot.contract.version == 0 and not bridge.records


def test_confirmed_historical_visit_is_not_rejected_as_future_requirement():
    d, session, bridge, rt = setup(patterns=((Atom("visit", "blue", "green"), Atom("forbid", "blue")),))
    m = d.model
    task = m.Contract(0, (m.Obligation(m.Clause(id="v", kind="visit", object="blue", target="green")),
                          m.Obligation(m.Clause(id="f", kind="forbid", object="blue"), 2)))
    rt.snapshot = m.Snapshot(task, m.State(), (m.Event(2, "place", "blue", "green"),), "supported").validate()
    result, _ = rt.assess()
    assert result.status == "ready" and not result.plan and session.backend_calls == 0


def test_unknown_history_preserves_need_observation_metadata():
    d, session, bridge, rt = setup(patterns=((Atom("visit", "blue", "green"), Atom("forbid", "blue")),))
    m = d.model
    task = m.Contract(0, (m.Obligation(m.Clause(id="v", kind="visit", object="blue", target="green")),
                          m.Obligation(m.Clause(id="f", kind="forbid", object="blue"), 2)))
    rt.snapshot = m.Snapshot(task, m.State(), (m.Event(2, "place", "blue", "green", "unknown"),), "supported").validate()
    result, _ = rt.assess()
    assert result == m.decide(rt.snapshot, task, 8)
    assert result.status == "need_observation" and result.feasible_alternatives == 1
    assert session.backend_calls == 1


def test_observation_rechecks_current_state_instead_of_reusing_old_conflict():
    atoms = (Atom("visit", "blue", "green"), Atom("forbid", "yellow"))
    d, session, bridge, rt = setup(atoms, (atoms,))
    rt.observe(d.model.State(yellow="green"), observation_id="blocked")
    assert rt.assess()[0].status == "conflict" and session.backend_calls == 0
    rt.observe(d.model.State(), observation_id="clear")
    assert rt.assess()[0].status == "ready" and session.backend_calls == 1


def test_command_is_not_observation_and_stale_ticket_cannot_execute():
    d, session, bridge, rt = setup((Atom("at", "red", "green"),))
    plan = rt.assess()[0].plan
    ticket = rt.issue()
    before = rt.snapshot
    command = rt.consume(ticket)
    assert command.object_id == "red_candidate" and rt.snapshot == before
    with pytest.raises(ValueError):
        rt.consume(ticket)
    with pytest.raises(ValueError):
        rt.issue()
    state, event = next((s, e) for s, e in d.model.successors(before.state, 1) if e == plan[0])
    rt.observe(state, observation_id="supported", events=(event,))
    assert rt.assess(plan[1:])[0].status == "ready"
    ticket = rt.issue()
    rt.observe(state, observation_id="new-observation")
    with pytest.raises(ValueError):
        rt.consume(ticket)


def test_stale_revision_and_nonconsecutive_version_rejected():
    d, _, _, rt = setup()
    proposed = replace(rt.snapshot.contract, version=1)
    with pytest.raises(ValueError, match="stale"):
        rt.accept(proposed, expected_digest="old", expected_generation=0)
    with pytest.raises(ValueError, match="nonconsecutive"):
        rt.accept(replace(proposed, version=2), expected_digest=rt.snapshot.digest, expected_generation=0)


def test_adapter_preserves_existing_runtime_and_requires_frozen_session():
    original = original_runtime.routed_decision
    d, session, bridge, _ = setup()
    assert bridge.unchanged_runtime_methods and original_runtime.routed_decision is original
    session.cache_writable = True
    with pytest.raises(ValueError, match="frozen"):
        build_fragment_runtime(d, session, IndependentActionBound(d))


def test_external_invalid_ready_plan_cannot_bypass_validation():
    d, _, _, rt = setup()
    proposed = replace(d.old.contract([Atom("at", "red", "green")]), version=1)
    invalid = d.model.Decision("ready", (), 1, 1, 8)
    with pytest.raises(ValueError, match="externally supplied"):
        rt.accept(proposed, expected_digest=rt.snapshot.digest, expected_generation=0, supplied_decision=invalid)
