"""Audit a frozen symbolic pilot; never report these as physical success."""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def analyze(source, manifest):
    cases=[json.loads(l) for l in manifest.read_text().splitlines() if l.strip()]
    rows=[json.loads(l) for l in source.read_text().splitlines() if l.strip()]
    expected={(c['case_id'],m) for c in cases for m in ['SYMBOLIC_SEARCH','MODEL_RESAMPLE','MODEL_FEEDBACK']}
    actual=[(r['case_id'],r['method']) for r in rows]
    errors=[]
    if set(actual)!=expected or len(actual)!=len(set(actual)):errors.append('missing_extra_or_duplicate_records')
    groups=defaultdict(list)
    for r in rows:
        groups[r['level'],r['method']].append(r)
        if r.get('physical_success') is not None:errors.append('unexpected_physical_claim')
        if len(r['audit'])>2:errors.append('call_budget_exceeded')
        if r['calls']!=len(r['audit']):errors.append('call_accounting_mismatch')
        if r['method'].startswith('MODEL_') and not r['audit']:errors.append('missing_model_attempt')
        for a in r['audit']:
            if 'content' not in a:errors.append('model_attempt_without_response')
            if a.get('adapter')!='fixed_right_arm_v1':errors.append('unexpected_adapter')
    output=[]
    for (level,method),rs in sorted(groups.items()):
        rejected=Counter()
        for r in rs:
            for a in r['audit']:
                if a['accepted']:continue
                e=a.get('rejection',{})
                if e.get('error_type'):kind='parse_or_schema:'+e['error_type']
                elif not e.get('constraints_satisfied',True):kind='forbidden_object'
                elif a.get('valid') and not a.get('goal_satisfied'):kind='valid_but_goal_incomplete'
                else:kind=e.get('error_code','other')
                rejected[kind]+=1
        output.append({'level':level,'method':method,'cases':len(rs),
            'symbolic_ready':sum(r['symbolic_ready'] for r in rs),
            'first_call_ready':sum(bool(r['audit'] and r['audit'][0]['accepted']) for r in rs),
            'second_call_ready':sum(bool(len(r['audit'])==2 and r['audit'][1]['accepted']) for r in rs),
            'calls':sum(r['calls'] for r in rs),
            'tokens':sum(a.get('total_tokens',0) for r in rs for a in r['audit']),
            'candidate_rejections_not_task_counts':dict(rejected)})
    keyed={(r['case_id'],r['method']):r for r in rows}
    wins=losses=ties=0
    for c in cases:
        a=keyed.get((c['case_id'],'MODEL_FEEDBACK'));b=keyed.get((c['case_id'],'MODEL_RESAMPLE'))
        if a is None or b is None:continue
        x,y=a['symbolic_ready'],b['symbolic_ready']
        wins+=x and not y;losses+=y and not x;ties+=x==y
    return {'source':str(source),'manifest':str(manifest),'records':len(rows),
        'scope':'symbolic_only_not_physical','complete':not errors,'audit_errors':sorted(set(errors)),
        'groups':output,'paired_feedback_vs_resample':{'wins':wins,'losses':losses,'ties':ties},
        'interpretation':'Assignment permutations are not independent physical tasks. No formal significance claim.'}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--manifest',type=Path,default=Path('data/scenarios/recovery_difficulty_v1.jsonl'))
    args=p.parse_args()
    print(json.dumps(analyze(args.source,args.manifest),ensure_ascii=False,indent=2))
