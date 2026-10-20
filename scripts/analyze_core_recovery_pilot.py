"""Audit paired core recovery runs without selecting favorable outcomes."""
import argparse
import json
from collections import Counter
from pathlib import Path

METHODS = ('MODEL_DIRECT_REPLAN', 'MODEL_COMPACT_REPAIR')


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
            reset_violations=reset_violations, evidence_missing=evidence_missing, methods=methods)
    return dict(conditions=conditions,
        technical_gate_passed=all(c['complete'] and not c['reset_violations'] and not c['evidence_missing']
                                  for c in conditions.values()),
        formal_ready=False,
        note='Human trace review required. No superiority, equivalence or history-necessity conclusion from this gate.')


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('root',type=Path)
    print(json.dumps(analyze(p.parse_args().root),indent=2))
