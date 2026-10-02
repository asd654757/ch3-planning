"""Completeness and paired outcomes for shared-candidate symbolic recovery."""
import argparse
import json
from collections import Counter
from pathlib import Path


def analyze(source,manifest):
    cases=[json.loads(l) for l in manifest.read_text().splitlines() if l.strip()]
    rows=[json.loads(l) for l in source.read_text().splitlines() if l.strip()]
    errors=[]
    ids=[r['case_id'] for r in rows]
    if set(ids)!={c['case_id'] for c in cases} or len(ids)!=len(set(ids)):
        errors.append('missing_extra_or_duplicate_cases')
    count=Counter();groups={}
    for row in rows:
        b=row['branches'];a=b['NO_ERROR_FEEDBACK'];f=b['ERROR_FEEDBACK']
        count['first_accepted']+=row['first_accepted']
        count['first_rejected']+=not row['first_accepted']
        count['missing_first_response']+=row['first_candidate_sha256'] is None
        count['actual_calls']+=row['actual_model_calls']
        count['no_feedback_ready']+=a['symbolic_ready'];count['feedback_ready']+=f['symbolic_ready']
        count['paired_wins']+=f['symbolic_ready'] and not a['symbolic_ready']
        count['paired_losses']+=a['symbolic_ready'] and not f['symbolic_ready']
        count['paired_ties']+=a['symbolic_ready']==f['symbolic_ready']
        if not row['first_accepted'] and row['first_candidate_sha256'] is not None:
            count['correction_eligible']+=1
            count['no_feedback_corrected']+=a['symbolic_ready'];count['feedback_corrected']+=f['symbolic_ready']
            if len(a['audit'])!=1 or len(f['audit'])!=1:errors.append('unequal_branch_budget')
            else:
                x=dict(a['audit'][0]['request']);y=dict(f['audit'][0]['request'])
                x.pop('rejection_feedback',None);y.pop('rejection_feedback',None)
                if x!=y:errors.append('requests_differ_beyond_feedback')
        for branch in b.values():
            if branch['shared_candidate_sha256']!=row['first_candidate_sha256']:errors.append('candidate_hash_mismatch')
        g=groups.setdefault(row['level'],Counter())
        g['cases']+=1;g['first_accepted']+=row['first_accepted']
        g['no_feedback_ready']+=a['symbolic_ready'];g['feedback_ready']+=f['symbolic_ready']
        if row['physical_success'] is not None:errors.append('unexpected_physical_claim')
    return {'source':str(source),'scope':'symbolic_shared_candidate_pilot_not_physical',
            'complete':not errors,'audit_errors':sorted(set(errors)),
            'counts':dict(count),'levels':{k:dict(v) for k,v in groups.items()},
            'interpretation':'Report both all-case and common-rejected-candidate denominators; no selected failures discarded.'}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--manifest',type=Path,default=Path('data/scenarios/recovery_difficulty_v1.jsonl'))
    args=p.parse_args()
    print(json.dumps(analyze(args.source,args.manifest),indent=2))
