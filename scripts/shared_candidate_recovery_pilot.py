"""One common model candidate, followed by equal-budget correction branches.

Symbolic mechanism study, not physical execution or an end-to-end benchmark.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

from ch3.capability.registry import load_registry
from ch3.repair.persistent_model_recovery import generate_suffix
from ch3.schema.model_plan import GoalSpec
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator
from ch3.vlm.client import DashScopeVLMClient
from scripts.recovery_difficulty_pilot import search


def run_case(case, client, *, index=0):
    state = WorldState(objects=set(case['objects']), at=dict(case['at']))
    registry = load_registry()
    registry._arms = {'right'}
    validator = Validator(state.objects, registry)
    goal = GoalSpec(facts=case['goal'])
    kwargs = dict(client=client, state=state, goal=goal, validator=validator,
                  history=case['history'], attempts=1, instruction=case['instruction'],
                  targets=case['targets'], protected_objects=case['protected_objects'],
                  execution_event=case['perturbation'], fixed_right_arm=True)
    first_plan, first_audit = generate_suffix(**kwargs)
    first = first_audit[0]
    content = first.get('content')
    candidate_hash = hashlib.sha256(content.encode()).hexdigest() if content is not None else None
    branch_order = ['NO_ERROR_FEEDBACK', 'ERROR_FEEDBACK']
    if index % 2:
        branch_order.reverse()
    branches = {}
    for method in branch_order:
        if first_plan is not None:
            plan, audit, status = first_plan, [], 'common_first_candidate_accepted'
        elif content is None:
            plan, audit, status = None, [], 'common_call_missing_response'
        else:
            plan, audit = generate_suffix(**kwargs, previous_candidate=content,
                initial_feedback=first['rejection'], feedback_enabled=method == 'ERROR_FEEDBACK')
            status = 'correction_accepted' if plan is not None else 'correction_rejected'
        branches[method] = {'symbolic_ready': plan is not None, 'status': status,
            'shared_candidate_sha256': candidate_hash, 'additional_calls': len(audit),
            'plan': plan.model_dump(mode='json') if plan is not None else None, 'audit': audit}
    reference, reason = search(case, state, goal, validator)
    return {'case_id': case['case_id'], 'level': case['level'],
        'scope': 'shared_candidate_symbolic_mechanism_pilot_not_physical',
        'first_candidate_sha256': candidate_hash, 'first_accepted': first_plan is not None,
        'first_audit': first_audit, 'branch_order': branch_order, 'branches': branches,
        'search_ready': reference is not None, 'search_reason': reason,
        'actual_model_calls': len(first_audit) + sum(b['additional_calls'] for b in branches.values()),
        'physical_success': None}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, default=Path('data/scenarios/recovery_difficulty_v1.jsonl'))
    p.add_argument('--env-file', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    cases = [json.loads(l) for l in args.manifest.read_text().splitlines() if l.strip()]
    if len({c['case_id'] for c in cases}) != len(cases):
        p.error('duplicate case IDs')
    client = DashScopeVLMClient(env_path=args.env_file, timeout=60, max_retries=0)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    calls = 0
    with args.output.open('x') as f:
        for index, case in enumerate(cases):
            row = run_case(case, client, index=index)
            f.write(json.dumps(row) + '\n')
            f.flush()
            calls += row['actual_model_calls']
            print(json.dumps({'case_id': row['case_id'], 'first_accepted': row['first_accepted'],
                'branches': {k: b['symbolic_ready'] for k, b in row['branches'].items()},
                'calls': row['actual_model_calls']}), flush=True)
    print(json.dumps({'completed_cases': len(cases), 'actual_model_calls': calls,
                      'output': str(args.output)}), flush=True)


if __name__ == '__main__':
    main()
