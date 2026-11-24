from dataclasses import replace

import pytest

from ch3.revision import history
from ch3.revision.candidate_runtime import CandidateFirstEvidenceRuntime, candidate_first_planner
from ch3.revision.evaluation import valid_suffix
from ch3.revision.evidence_resolution import EvidenceFinding


def inspection_snapshot(*, protected=False):
    obligations = [
        history.Obligation(history.Clause(id="delivery", kind="at", object="blue", target="return")),
    ]
    if protected:
        obligations.append(history.Obligation(history.Clause(id="keep", kind="forbid", object="yellow")))
    else:
        obligations.append(history.Obligation(history.Clause(id="visit", kind="visit", object="blue", target="green")))
    events = (history.Event(1, "place", "yellow" if protected else "blue", "green", "unknown", "possible-inspection"),)
    return history.Snapshot(history.Contract(1, tuple(obligations)), history.State(), events, "observed")


@pytest.mark.parametrize("reject_candidate", [False, True])
def test_candidate_is_checked_on_original_history_before_release(reject_candidate):
    snap = inspection_snapshot()
    previous_checks = []
    solved = []

    def solve(scored, contract, horizon):
        solved.append(scored.history[0].evidence)
        return history.decide(scored, contract, horizon)

    def verify(scored, contract, plan):
        previous_checks.append(scored)
        return not reject_candidate and valid_suffix(scored, contract, plan)

    planner = candidate_first_planner(decide=solve, monitor=history.monitor,
                                      possible_histories=history.possible_histories, validate_suffix=verify)
    result = planner(snap, snap.contract, 8)
    assert previous_checks == [snap]
    assert solved == (["refuted", "unknown"] if reject_candidate else ["refuted"])
    assert result == history.decide(snap, snap.contract, 8)
    assert result.status == "ready" and len(result.plan) == 4 and result.history_alternatives == 2


def test_conflicting_prefix_defers_and_only_refutation_authorizes():
    snap = inspection_snapshot(protected=True)
    original = replace(snap, contract=history.Contract(0, ()))
    runtime = CandidateFirstEvidenceRuntime(original)
    before, _ = runtime.accept(snap.contract, expected_digest=original.digest, expected_generation=0)
    assert before.status == "need_observation"
    with pytest.raises(ValueError, match="no executable"):
        runtime.issue()
    current = runtime.snapshot
    runtime.resolve_evidence(EvidenceFinding(1, "refuted", "new-frame", current.digest, runtime.generation))
    after, _ = runtime.assess()
    assert after.status == "ready" and runtime.original_history == snap.history
    assert runtime.consume(runtime.issue()).skill == "pick"
