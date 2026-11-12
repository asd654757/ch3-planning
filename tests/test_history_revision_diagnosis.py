from dataclasses import replace

import pytest

from ch3.revision.cases import make_cases
from ch3.revision.diagnosis import conflict_core, diagnose, minimal_evidence_query, remove_clauses, repair_options
from ch3.revision.history import Clause, Contract, Edit, Event, Obligation, Patch, decide
from ch3.revision.session import RevisionSession


def case(family):
    return next(c for c in make_cases() if c["family"] == family)


@pytest.mark.parametrize("family", ("past_order", "capacity", "held_prohibition", "changed_waypoint_order"))
def test_core_recheck_and_deletion_minimality(family):
    c = case(family)
    ids = conflict_core(c["snapshot"], c["gold"])
    core = Contract(c["gold"].version, tuple(o for o in c["gold"].obligations if o.clause.id in ids))
    assert core.validate()
    assert decide(c["snapshot"], core).status == "conflict"
    for id in ids:
        assert decide(c["snapshot"], remove_clauses(core, {id})).status != "conflict"


def test_query_ignores_irrelevant_unknown_event():
    c = case("uncertain_order")
    s = c["snapshot"]
    s = replace(s, history=(*s.history, Event(9, "place", "yellow", "table", "unknown")))
    assert minimal_evidence_query(s, c["gold"]) == (2,)
    assert diagnose(s, c["gold"])["query_source"] == "archived_event_evidence_or_user_scope_clarification"


def test_restart_is_an_explicit_task_change_and_preserves_actual_history():
    c = case("past_order")
    before = c["snapshot"].history
    options = repair_options(c["snapshot"], c["gold"])
    restart = [o for o in options if o.kind == "prospective_restart"]
    assert restart
    for option in options:
        assert option.requires_explicit_revision and not option.original_task_satisfied
        assert decide(c["snapshot"], option.proposed).status == "ready"
    assert c["snapshot"].history == before


def test_pending_revision_stops_old_task_and_followup_edits_pending_intent():
    c = case("past_order")
    s = RevisionSession(c["snapshot"])
    order = next(o.clause for o in c["gold"].obligations if o.clause.id == "order")
    patch = Patch(version=0, snapshot=s.working_snapshot.digest,
                  edits=[Edit(op="add", id="order", clause=order, quote=c["update"])])
    assert s.submit(patch, c["update"]).decision.status == "conflict"
    with pytest.raises(ValueError, match="suspends"):
        _ = s.executable_contract
    pending = s.working_snapshot
    assert pending.contract.version == 1
    followup = Patch(version=1, snapshot=pending.digest,
        edits=[Edit(op="remove", id="order", quote="取消顺序要求")])
    assert s.submit(followup, "取消顺序要求").decision.status == "ready"
    assert s.executable_contract.version == 2
    assert s.active.history == c["snapshot"].history
    assert len(s.audit) == 2


def test_empty_repair_set_for_a_nonconflicting_request():
    c = case("retarget")
    assert repair_options(c["snapshot"], c["gold"]) == ()
    assert conflict_core(c["snapshot"], c["gold"]) == ()
