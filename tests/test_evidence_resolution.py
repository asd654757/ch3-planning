from dataclasses import replace

import pytest

from ch3.revision.evidence_resolution import EvidenceFinding, EvidenceRevisionRuntime
from ch3.revision.history import Clause, Contract, Event, Obligation, Snapshot, State


def runtime():
    clauses = (Clause(id="visit", kind="visit", object="blue", target="green"),
               Clause(id="protect", kind="forbid", object="blue"))
    task = Contract(0, (Obligation(clauses[0]), Obligation(clauses[1], 1)))
    event = Event(1, "place", "blue", "green", "unknown", "original-observation")
    return EvidenceRevisionRuntime(Snapshot(task, State(blue="green"), (event,), "current-observation"))


@pytest.mark.parametrize(("verdict", "status"), [("confirmed", "ready"), ("refuted", "conflict")])
def test_findings_preserve_original_event_and_reassess_same_contract(verdict, status):
    rt = runtime()
    previous = rt.snapshot
    assert rt.assess()[0].status == "need_observation"
    finding = EvidenceFinding(1, verdict, "additional-observation", previous.digest, rt.generation)
    current = rt.resolve_evidence(finding)
    assert current.state == previous.state and current.contract == previous.contract
    assert current.cursor == previous.cursor
    assert rt.original_history == previous.history
    assert rt.findings[0].finding == finding
    assert current.history == (replace(previous.history[0], evidence=verdict),)
    with pytest.raises(ValueError, match="no executable"):
        rt.issue()
    assert rt.assess()[0].status == status


def test_findings_are_snapshot_bound_fresh_and_final():
    rt = runtime()
    finding = EvidenceFinding(1, "confirmed", "additional-observation", rt.snapshot.digest, rt.generation)
    rt.observe(rt.snapshot.state, observation_id="next-frame")
    before = rt.snapshot
    with pytest.raises(ValueError, match="stale"):
        rt.resolve_evidence(finding)
    current = replace(finding, snapshot_digest=before.digest, generation=rt.generation)
    with pytest.raises(ValueError, match="fresh"):
        rt.resolve_evidence(replace(current, observation_id="original-observation"))
    assert rt.snapshot is before and not rt.findings
    rt.resolve_evidence(current)
    with pytest.raises(ValueError, match="unresolved"):
        rt.resolve_evidence(replace(current, observation_id="second-claim", snapshot_digest=rt.snapshot.digest,
                                    generation=rt.generation, verdict="refuted"))
    assert len(rt.findings) == 1 and rt.snapshot.history[0].evidence == "confirmed"


def test_resolution_revokes_existing_ticket_even_if_goal_and_suffix_are_unchanged():
    task = Contract(0, (Obligation(Clause(id="goal", kind="at", object="yellow", target="return")),))
    dispute = Event(1, "pick", "blue", None, "unknown", "possible-blue-pick")
    rt = EvidenceRevisionRuntime(Snapshot(task, State(), (dispute,), "current"))
    assert rt.assess()[0].status == "ready"
    ticket = rt.issue()
    rt.resolve_evidence(EvidenceFinding(1, "refuted", "new-frame", rt.snapshot.digest, rt.generation))
    with pytest.raises(ValueError, match="stale"):
        rt.consume(ticket)
    assert rt.assess()[0].status == "ready"
    assert rt.issue() != ticket
