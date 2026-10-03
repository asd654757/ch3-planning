"""Offline audit and expansion gate; evaluation truth never selects runtime actions."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path


def analyze(rows, expected_seeds):
    methods = ('MODEL_NO_PLAN_REPAIR', 'MODEL_DIAGNOSTIC_REPAIR')
    paired = defaultdict(dict)
    counts = Counter((r['seed'], r['method']) for r in rows)
    complete = len(rows)==2*len(expected_seeds) and set(counts)=={
        (s,m) for s in expected_seeds for m in methods} and all(n==1 for n in counts.values())
    result = dict(completed_episodes=len(rows), paired_complete=complete, methods={}, failures=[])
    for method in methods:
        selected = [r for r in rows if r['method']==method]
        calls = [a for r in selected for a in r.get('model_audit',[]) if a.get('phase')=='execution_repair']
        result['methods'][method] = dict(episodes=len(selected), successes=sum(r['success'] for r in selected),
            actual_model_calls=sum(r['model_calls'] for r in selected),
            execution_repair_calls=sum(a['actual_model_calls'] for a in calls),
            accepted_execution_repairs=sum(bool(a.get('accepted')) for a in calls),
            successful_episodes_with_accepted_execution_repair=sum(r['success'] and any(
                a.get('phase')=='execution_repair' and a.get('accepted') for a in r.get('model_audit',[])) for r in selected))
    false_completion = []
    for r in rows:
        paired[r['seed']][r['method']] = bool(r['success'])
        if not r['success']:
            result['failures'].append(dict(seed=r['seed'],method=r['method'],error=r.get('error'),stage=r.get('failure_stage')))
            # Terminal scores exist only after online completion. Offline mismatch is an audit, not a recovery trigger.
            if r.get('terminal_scores') and not all(x['arrived'] for x in r['terminal_scores'].values()):
                false_completion.append(dict(seed=r['seed'],method=r['method']))
    result['online_completion_terminal_goal_mismatches'] = false_completion
    if complete:
        result['paired_outcomes'] = dict(Counter(
            'both_success' if all(d.values()) else 'both_failure' if not any(d.values())
            else 'repair_win' if d[methods[1]] else 'repair_loss' for d in paired.values()))
    recovery = result['methods'][methods[1]]
    reasons=[]
    if not complete: reasons.append('incomplete_or_duplicate_pairs')
    if false_completion: reasons.append('online_completion_disagrees_with_terminal_goal')
    if recovery['successful_episodes_with_accepted_execution_repair']==0:
        reasons.append('no_accepted_execution_repair_with_successful_completion')
    result['recovery_expansion_gate'] = dict(passed=not reasons, reasons=reasons,
        scope='engineering_readiness_not_model_superiority_or_formal_safety_certificate')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir',type=Path,required=True)
    args=parser.parse_args()
    protocol=json.loads((args.input_dir/'protocol.json').read_text())
    rows=[json.loads(line) for line in (args.input_dir/'episodes.jsonl').read_text().splitlines()]
    result=analyze(rows,protocol['seeds'])
    (args.input_dir/'feedback_audit.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
