"""Analyze complete paired final comparison; refuse incomplete/duplicate samples."""
import argparse
import collections
import json
import math
from pathlib import Path


def wilson(k, n):
    z = 1.95996398454
    den = 1 + z*z/n
    mid = (k/n + z*z/(2*n))/den
    half = z*math.sqrt((k/n)*(1-k/n)/n + z*z/(4*n*n))/den
    return [mid-half, mid+half]


def analyze(path):
    events = [json.loads(line) for line in Path(path).read_text().splitlines()]
    protocol = next(e for e in events if e['event'] == 'comparison_protocol')
    if not any(e['event'] == 'comparison_summary' for e in events):
        raise ValueError('No completion summary; do not analyze partial run as final')
    rows = [e for e in events if e['event'] == 'case_summary']
    expected = {(s,m) for s in range(protocol['start_seed'], protocol['start_seed']+protocol['seed_count'])
                for m in protocol['methods']}
    if len(rows) != len(expected) or {(r['seed'],r['method']) for r in rows} != expected:
        raise ValueError('Missing or duplicate paired cases')
    summaries = {}
    for m in protocol['methods']:
        rr = [r for r in rows if r['method'] == m]
        n = len(rr); k = sum(r['confirmed_complete'] for r in rr)
        reasons = collections.Counter(str(e) for r in rr for e in r['rejection_reasons'])
        summaries[m] = {'n':n,'successes':k,'success_rate':k/n,'wilson_95':wilson(k,n),
            'realized_disturbances':sum(r['perturbation_executed'] for r in rr),
            'first_repair_accepted':sum(r['first_repair_accepted'] is True for r in rr),
            'mean_logical_calls':sum(r['model_calls'] for r in rr)/n,
            'actual_api_calls':sum(r['actual_api_calls'] for r in rr),
            'recovery_tokens':sum(u['total_tokens'] for r in rr for u in r['model_usage']),
            'mean_commands':sum(r['commands'] for r in rr)/n,
            'rejections':dict(reasons),
            'failed_seeds':[r['seed'] for r in rr if not r['confirmed_complete']]}
    full = {r['seed']:r for r in rows if r['method']=='SUPERVISED_RECOVERY'}
    tests={}
    for m in protocol['methods']:
        if m == 'SUPERVISED_RECOVERY':continue
        base = {r['seed']:r for r in rows if r['method']==m}
        b=sum(full[s]['confirmed_complete'] and not base[s]['confirmed_complete'] for s in full)
        c=sum(base[s]['confirmed_complete'] and not full[s]['confirmed_complete'] for s in full)
        p=min(1.,2*sum(math.comb(b+c,i) for i in range(min(b,c)+1))/2**(b+c)) if b+c else 1.
        tests[m]={'full_only_success':b,'baseline_only_success':c,'mcnemar_exact_two_sided':p,
                  'bonferroni_p_two_comparisons':min(1.,p*2)}
    return {'protocol':protocol,'by_method':summaries,'paired_tests':tests,
            'limitations':['Repeated scenario types, not unseen tasks',
                'Privileged structured state, not visual perception',
                'Recovery tokens exclude shared initial-plan generation',
                'Ablation includes rejection feedback; interpret conflict-field contribution separately']}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('journal',type=Path)
    args=parser.parse_args()
    print(json.dumps(analyze(args.journal),ensure_ascii=False,indent=2))
