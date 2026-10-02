"""All-case and subset reporting; author-created pilot, not generalization proof."""
import argparse
import json
from pathlib import Path


def analyze(path):
    manifest=json.loads((path/'manifest.json').read_text())
    cases={r['case_id']:r for r in manifest['cases']}
    records=[json.loads(s) for s in (path/'records.jsonl').read_text().splitlines()]
    ids=[r['case_id'] for r in records]
    if len(ids)!=len(set(ids)) or set(ids)-set(cases):raise ValueError('duplicate/unexpected case')
    summary=dict(protocol=manifest['protocol'],expected=len(cases),completed=len(records),
        missing_case_ids=sorted(set(cases)-set(ids)),physical_execution=False,
        costs_are_shared_parse_not_independent_reruns=True,by_subset={})
    for subset in ['all','controlled','paraphrase','refusal']:
        expected=[c for c in cases.values() if subset=='all' or c['subset']==subset]
        actual=[r for r in records if subset=='all' or r['subset']==subset]
        group=dict(expected=len(expected),completed=len(actual),by_variant={})
        for variant in manifest['variants']:
            values=[r['results'][variant] for r in actual]
            group['by_variant'][variant]=dict(
                semantic_exact=sum(bool(v.get('semantic_exact')) for v in values),
                strict_success=sum(bool(v.get('strict_success')) for v in values),
                gold_plan_success=sum(bool(v.get('gold_plan_success')) for v in values),
                correct_refusal=sum(bool(v.get('correct_refusal')) for v in values),
                misaccept=sum(bool(v.get('misaccept')) for v in values),
                parse_or_plan_errors=sum('error' in v for v in values),
                ready_expected=sum(c['gold']['status']=='ready' for c in expected),
                refusal_expected=sum(c['gold']['status']!='ready' for c in expected))
        summary['by_subset'][subset]=group
    model_vs_rule={'both':0,'model_only':0,'rule_only':0,'neither':0}
    for r in records:
        m=bool(r['results']['model_semantics_search']['strict_success'])
        b=bool(r['results']['rule_semantics_search']['strict_success'])
        key='both' if m and b else 'model_only' if m else 'rule_only' if b else 'neither'
        model_vs_rule[key]+=1
    summary['paired_model_search_vs_rule_search']=model_vs_rule
    calls=[json.loads(f.read_text()) for f in path.glob('*/*_call.json')]
    summary['logged_calls']=len(calls)
    summary['total_tokens']=sum(c.get('total_tokens',0) for c in calls)
    summary['semantic_tokens']=sum(c.get('total_tokens',0) for f in path.glob('*/semantic_call.json') for c in [json.loads(f.read_text())])
    summary['planning_tokens']=sum(c.get('total_tokens',0) for f in path.glob('*/plan_call.json') for c in [json.loads(f.read_text())])
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path);a=p.parse_args()
    data=analyze(a.directory)
    (a.directory/'analysis.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
    print(json.dumps(data,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
