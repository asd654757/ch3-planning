"""Read-only paired analysis; never select cases according to the winner."""
import argparse
import json
from math import comb
from pathlib import Path


def analyze(root):
    result = {}
    for condition in ('medium', 'hard'):
        path = root / condition / 'episodes.jsonl'
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
        groups = {}
        for row in rows:
            key = (row['seed'], row['method'])
            if key in groups:
                raise ValueError(f'duplicate {key}')
            groups[key] = row
        methods = ['MODEL_DIRECT_REPLAN', 'MODEL_CONSTRAINT_REPAIR']
        paired = [seed for seed in sorted({r['seed'] for r in rows})
                  if all((seed, method) in groups for method in methods)]
        wins = losses = 0
        for seed in paired:
            direct, full = [groups[seed, method] for method in methods]
            wins += bool(full['success'] and not direct['success'])
            losses += bool(direct['success'] and not full['success'])
        discordant = wins + losses
        p = min(1., 2 * sum(comb(discordant, i) for i in range(min(wins, losses)+1))
                / 2**discordant) if discordant else 1.
        result[condition] = {'episodes': len(rows), 'complete_pairs': len(paired),
            'full_only_success': wins, 'direct_only_success': losses,
            'exploratory_exact_mcnemar_p': p, 'methods': {}}
        for method in methods:
            subset = [r for r in rows if r['method'] == method]
            calls = [a for r in subset for a in r['model_audit']
                     if a['phase'] != 'initial' and a['actual_model_calls']]
            result[condition]['methods'][method] = {
                'episodes': len(subset), 'successes': sum(r['success'] for r in subset),
                'recovery_calls': len(calls),
                'recovery_tokens': sum(a.get('total_tokens', 0) for a in calls),
                'recovery_latency_ms': sum(a.get('latency_ms') or 0 for a in calls),
                'rejected_recovery_candidates': sum(not a['accepted'] for a in calls),
                'failures': [{'seed': r['seed'], 'error': r.get('error')} for r in subset if not r['success']]}
    return {'scope': 'exploratory_not_confirmatory', 'conditions': result}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.root), ensure_ascii=False, indent=2))
