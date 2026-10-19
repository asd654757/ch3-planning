"""Paired held-out layout validation, including failures and missing records."""
import argparse
import json
from math import comb
from pathlib import Path

METHODS = ('MODEL_DIRECT_REPLAN', 'MODEL_CONSTRAINT_REPAIR', 'MODEL_COMPACT_REPAIR')


def costs(row):
    calls = [a for a in row['model_audit'] if a['phase'] != 'initial' and a['actual_model_calls']]
    return {'calls': len(calls), 'tokens': sum(a.get('total_tokens', 0) for a in calls),
            'rejections': sum(not a['accepted'] for a in calls)}


def analyze(root):
    manifest = json.loads((root / 'validation_protocol.json').read_text())
    seeds = manifest['seeds']
    result = {'scope': 'held_out_layouts_not_new_task_semantics', 'conditions': {}}
    for condition in ('medium', 'hard'):
        path = root / condition / 'episodes.jsonl'
        rows = [json.loads(s) for s in path.read_text().splitlines() if s.strip()] if path.exists() else []
        indexed = {}
        for row in rows:
            key = (row['seed'], row['method'])
            if key in indexed or key[0] not in seeds or key[1] not in METHODS:
                raise ValueError(f'duplicate or unexpected record: {key}')
            indexed[key] = row
        missing = [[s, m] for s in seeds for m in METHODS if (s, m) not in indexed]
        summary = {'expected_episodes': len(seeds)*3, 'completed_episodes': len(rows),
                   'complete': not missing, 'missing': missing, 'methods': {}, 'comparisons': {}}
        for method in METHODS:
            subset = [r for r in rows if r['method'] == method]
            cs = [costs(r) for r in subset]
            summary['methods'][method] = {
                'completed': len(subset), 'successes': sum(r['success'] for r in subset),
                'recovery_cost': {k: sum(c[k] for c in cs) for k in ('calls', 'tokens', 'rejections')},
                'failures': [{'seed': r['seed'], 'error': r.get('error')} for r in subset if not r['success']]}
        for reference in METHODS[:2]:
            pairs = [(indexed[s, reference], indexed[s, METHODS[2]]) for s in seeds
                     if (s, reference) in indexed and (s, METHODS[2]) in indexed]
            wins = sum(bool(b['success'] and not a['success']) for a, b in pairs)
            losses = sum(bool(a['success'] and not b['success']) for a, b in pairs)
            n = wins + losses
            p = min(1., 2*sum(comb(n, i) for i in range(min(wins, losses)+1))/2**n) if n else 1.
            summary['comparisons']['compact_vs_'+reference] = {
                'paired_layouts': len(pairs), 'compact_only_success': wins, 'reference_only_success': losses,
                'exact_mcnemar_p_unadjusted': p,
                'per_seed_cost_deltas': [dict(seed=a['seed'],
                    **{k: costs(b)[k]-costs(a)[k] for k in ('calls', 'tokens', 'rejections')}) for a, b in pairs],
                'note': 'Negative deltas favor compact. Report costs with success; no noninferiority claim from ties.'}
        result['conditions'][condition] = summary
    result['complete'] = all(c['complete'] for c in result['conditions'].values())
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.root), ensure_ascii=False, indent=2))
