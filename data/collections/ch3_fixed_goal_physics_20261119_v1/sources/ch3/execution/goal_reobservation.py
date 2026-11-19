"""Bounded evidence acquisition; never infer completion from UNKNOWN."""
from copy import deepcopy


def resolve_goal_evidence(evidence, *, budget, decide, observe, emit):
    if budget not in (0, 1):
        raise ValueError('goal_reobservation_budget_must_be_zero_or_one')
    current = deepcopy(evidence)
    used = 0
    while current.get('status') == 'unknown' and used < budget:
        decision = decide(deepcopy(current), budget-used)
        emit(dict(event='goal_observation_decision', evidence=deepcopy(current),
                  decision=decision, remaining_observations=budget-used))
        if decision != 'observe':
            return current
        used += 1
        current = deepcopy(observe())
        emit(dict(event='goal_reobservation_result', evidence=deepcopy(current),
                  observations_used=used))
    if current.get('status') not in {'satisfied', 'not_satisfied', 'unknown'}:
        raise ValueError('invalid_goal_evidence_status')
    return current
