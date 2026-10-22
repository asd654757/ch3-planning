"""Audit paired core recovery runs without selecting favorable outcomes."""
import argparse
import json
from collections import Counter
from math import comb
from pathlib import Path

METHODS = ('MODEL_DIRECT_REPLAN', 'MODEL_COMPACT_REPAIR')


def exact_mcnemar(success_pairs):
    """Two-sided exact McNemar test from paired binary outcomes."""
    direct_only = sum(d and not c for d, c in success_pairs)
    compact_only = sum(c and not d for d, c in success_pairs)
    discordant = direct_only + compact_only
    if discordant == 0:
        p_value = 1.0
    else:
        tail = sum(comb(discordant, k) for k in range(min(direct_only, compact_only) + 1))
        p_value = min(1.0, 2.0 * tail / (2 ** discordant))
    return dict(direct_only_success=direct_only, compact_only_success=compact_only,
                discordant_pairs=discordant, exact_two_sided_p=p_value)


def paired_summary(rows):
    """Summarize method contrasts using seed as the matched unit."""
    indexed = {(r['seed'], r['method']): r for r in rows}
    seeds = sorted({r['seed'] for r in rows
                    if (r['seed'], METHODS[0]) in indexed
                    and (r['seed'], METHODS[1]) in indexed})
    pairs = [(indexed[(seed, METHODS[0])], indexed[(seed, METHODS[1])])
             for seed in seeds]
    success_pairs = [(bool(d['success']), bool(c['success'])) for d, c in pairs]
    cost_differences = {}
    for label, getter in (
        ('recovery_calls', lambda r: sum(a.get('actual_model_calls', 0) for a in r.get('model_audit', [])
                                         if a.get('phase') != 'initial')),
        ('recovery_tokens', lambda r: sum(a.get('total_tokens', 0) or 0 for a in r.get('model_audit', [])
                                           if a.get('phase') != 'initial')),
        ('physical_control_steps', lambda r: sum(e.get('execution', {}).get('steps', 0)
                                                  for e in r.get('events', [])
                                                  if isinstance(e, dict))),
    ):
        deltas = [getter(c) - getter(d) for d, c in pairs]
        cost_differences[label] = dict(
            direct_total=sum(getter(d) for d, _ in pairs),
            compact_total=sum(getter(c) for _, c in pairs),
            compact_minus_direct=deltas,
            mean_compact_minus_direct=(sum(deltas) / len(deltas) if deltas else None),
            compact_lower=sum(delta < 0 for delta in deltas),
            tied=sum(delta == 0 for delta in deltas),
            direct_lower=sum(delta > 0 for delta in deltas),
        )
    direct_successes = sum(d for d, _ in success_pairs)
    compact_successes = sum(c for _, c in success_pairs)
    if compact_successes > direct_successes:
        outcome = 'compact_higher_observed_success'
    elif compact_successes < direct_successes:
        outcome = 'direct_higher_observed_success'
    else:
        outcome = 'success_counts_tied'
    if len(seeds) < 20:
        inferential_status = 'descriptive_only_small_paired_sample'
    else:
        inferential_status = 'paired_test_reported_not_equivalence_or_noninferiority'
    return dict(paired_seeds=seeds, paired_n=len(seeds),
        direct_successes=direct_successes, compact_successes=compact_successes,
        compact_minus_direct_successes=compact_successes-direct_successes,
        success_pairs=exact_mcnemar(success_pairs), outcome_summary=outcome,
        costs=cost_differences, inferential_status=inferential_status,
        warning='A nonsignificant paired test is not evidence of equivalence or noninferiority.')


def analyze(root):
    manifest = json.loads((root/'pilot_manifest.json').read_text())
    conditions = {}
    for condition in manifest['conditions']:
        path = root/condition/'episodes.jsonl'
        rows = [json.loads(s) for s in path.read_text().splitlines() if s.strip()] if path.exists() else []
        counts = Counter((r['seed'], r['method']) for r in rows)
        expected = {(s, m) for s in manifest['seeds'] for m in METHODS}
        methods = {}
        for method in METHODS:
            subset = [r for r in rows if r['method'] == method]
            audit = [a for r in subset for a in r.get('model_audit', []) if a['phase'] != 'initial']
            methods[method] = dict(episodes=len(subset), successes=sum(r['success'] for r in subset),
                recovery_calls=sum(a.get('actual_model_calls', 0) for a in audit),
                recovery_tokens=sum(a.get('total_tokens', 0) or 0 for a in audit),
                candidate_rejections=sum(not a.get('accepted', False) for a in audit),
                failures=[dict(seed=r['seed'], error=r.get('error'), stage=r.get('failure_stage'))
                          for r in subset if not r['success']])
        reset_violations = [dict(seed=r['seed'], method=r['method']) for r in rows
                            if r.get('runtime_episode_reset_count') != 1]
        evidence_missing = [dict(seed=r['seed'], method=r['method']) for r in rows
            if any('recovery_evidence' not in a['request'] for a in r.get('model_audit', []))]
        conditions[condition] = dict(complete=set(counts)==expected and all(n==1 for n in counts.values()),
            missing=sorted(expected-set(counts)), unexpected=sorted(set(counts)-expected),
            duplicates=[list(k) for k,n in counts.items() if n>1],
            reset_violations=reset_violations, evidence_missing=evidence_missing, methods=methods,
            paired=paired_summary(rows))
    return dict(conditions=conditions,
        technical_gate_passed=all(c['complete'] and not c['reset_violations'] and not c['evidence_missing']
                                  for c in conditions.values()),
        formal_ready=False,
        primary_endpoint='paired recovery success and protocol-correct terminal outcome; costs are separate secondary endpoints',
        note='Human trace review required. No superiority, equivalence or history-necessity conclusion from this gate.')


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('root',type=Path)
    print(json.dumps(analyze(p.parse_args().root),indent=2))
