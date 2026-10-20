"""Failure-inclusive acquisition pilot audit; no automatic formal-run authorization."""
import argparse
from collections import Counter
import json
from pathlib import Path


def analyze(root):
    protocol=json.loads((root/'protocol.json').read_text())
    path=root/'episodes.jsonl'
    rows=[json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    expected={(seed,method) for seed in protocol['seeds'] for method in
        ['MODEL_DIRECT_REPLAN','MODEL_COMPACT_REPAIR']}
    counts=Counter((r['seed'],r['method']) for r in rows)
    methods={}
    for method in ['MODEL_DIRECT_REPLAN','MODEL_COMPACT_REPAIR']:
        selected=[r for r in rows if r['method']==method]
        audit=[a for r in selected for a in r.get('model_audit',[]) if a['phase']!='initial']
        methods[method]=dict(episodes=len(selected),successes=sum(r['success'] for r in selected),
            acquisition_requests=sum(a['phase']=='observation_decision' for a in audit),
            acquisitions_resolved=sum(bool(r.get('entry_observation_resolved')) for r in selected),
            recovery_calls=sum(a.get('actual_model_calls',0) for a in audit),
            recovery_tokens=sum(a.get('total_tokens',0) or 0 for a in audit),
            errors=dict(Counter(r.get('error') for r in selected if not r['success'])),
            resets=[r.get('episode_resets') for r in selected])
    return dict(complete=set(counts)==expected and all(n==1 for n in counts.values()),
        missing=sorted(expected-set(counts)),duplicates=[list(k) for k,n in counts.items() if n>1],
        unexpected=sorted(set(counts)-expected),methods=methods,
        formal_ready=False,
        note='Pilot completion is not superiority or readiness. Review physical and evidence failures before formal freeze.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('root',type=Path)
    args=p.parse_args()
    print(json.dumps(analyze(args.root),indent=2))
