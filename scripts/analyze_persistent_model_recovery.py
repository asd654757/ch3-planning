"""Audit completeness and report every task, including model rejection."""
import argparse
import json
from collections import defaultdict
from pathlib import Path


def analyze(path, seeds):
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    methods = ['RETRY_SAME_SUFFIX','OBSERVED_SEARCH','MODEL_DIRECT','MODEL_VERIFIED']
    expected = {(s,p,m) for s in range(seeds) for p in ['none','post_grasp_slip'] for m in methods}
    actual = [(r['seed'],r['perturbation'],r['method']) for r in rows]
    errors=[]
    if set(actual)!=expected or len(actual)!=len(set(actual)):
        errors.append('missing_extra_or_duplicate_tasks')
    groups=defaultdict(list)
    for r in rows:
        groups[r['perturbation'],r['method']].append(r)
        if r['reset_count']!=1: errors.append('reset_violation')
        if r['perturbation']=='post_grasp_slip' and not r['perturbation_applied']:
            errors.append('disturbance_not_realized')
        audit=r.get('model_audit',[])
        if len(audit)> (1 if r['method']=='MODEL_DIRECT' else 2):errors.append('call_budget_violation')
        for call in audit:
            if call.get('rejection',{}).get('error_type')=='AttributeError':errors.append('integration_error')
            if 'content' not in call:errors.append('model_call_without_response')
        if r['perturbation']=='post_grasp_slip' and r['method'].startswith('MODEL_') and not audit:
            errors.append('missing_model_attempt')
    return {'source':str(path),'tasks':len(rows),'expected_tasks':len(expected),
        'protocol_complete':not errors,'gate_errors':sorted(set(errors)),
        'scope':'simulator_state_feedback_not_visual_full_ROUTED',
        'groups':[{'perturbation':p,'method':m,'tasks':len(rs),
                   'actual_success':sum(r['success'] for r in rs),
                   'calls':sum(r['vlm_calls'] for r in rs),
                   'tokens':sum(r.get('model_tokens',0) for r in rs)}
                  for (p,m),rs in sorted(groups.items())]}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--seeds',type=int,required=True)
    args=parser.parse_args()
    print(json.dumps(analyze(args.source,args.seeds),indent=2))
