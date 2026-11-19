"""Candidate-based diagnostics; never reads evaluation labels or invents plans."""
from dataclasses import asdict

from ch3.revision.history import decide, monitor, possible_histories, successors


def explain_rejection(snapshot, contract, decision):
    """Strong direct baseline gets the monitor's evidence, not a repaired suffix."""
    state = snapshot.state
    prefix = []
    for index, action in enumerate(decision.plan):
        legal = [(ns, e) for ns, e in successors(state, snapshot.cursor+index+1)
                 if (e.kind, e.object, e.target, e.seq) == (action.kind, action.object, action.target, action.seq)]
        if not legal:
            return {"type": "ACTION_PRECONDITION", "first_invalid_action": index+1,
                "state_before_action": asdict(state), "candidate_action": asdict(action),
                "rules": "Pick requires empty hand; place requires holding that object and a free destination. Table has unlimited capacity."}
        state = legal[0][0]
        prefix.append(action)
    histories = tuple(possible_histories(snapshot.history))
    checks = [monitor(contract, state, (*h, *prefix)) for h in histories]
    reference = decide(snapshot, contract, decision.horizon)
    # Counts expose the result of bounded verification, available to all arms;
    # no reference plan is passed to the model or used for direct dispatch.
    return {"type": "HISTORY_OR_COMPLETION", "candidate_status": decision.status,
        "history_alternatives": reference.history_alternatives,
        "feasible_alternatives_within_budget": reference.feasible_alternatives,
        "violated_clause_ids_by_alternative": [sorted(v) for v, _ in checks],
        "pending_clause_ids_by_alternative": [sorted(p) for _, p in checks],
        "interpretation": "Check whether every alternative admits one common suffix, some alternatives are feasible, or none is feasible. Unknown events remain unconfirmed."}
