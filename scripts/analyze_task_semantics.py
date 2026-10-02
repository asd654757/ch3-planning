"""Analyze all launched task-semantic pilot records; no outcome selection."""
import argparse
import json
from pathlib import Path


def analyze(directory):
    manifest = json.loads((directory/'manifest.json').read_text())
    rows = [json.loads(s) for s in (directory/'records.jsonl').read_text().splitlines()]
    cases = {c['case_id']:c for c in manifest['cases']}
    ids = [r['case_id'] for r in rows]
    if len(ids) != len(set(ids)) or set(ids)-set(cases):
        raise ValueError('duplicate or unexpected case')
    ready = [r for r in rows if cases[r['case_id']]['gold']['status']=='ready']
    refuse = [r for r in rows if cases[r['case_id']]['gold']['status']!='ready']
    return dict(protocol=manifest['protocol'], expected=len(cases), completed=len(rows),
        missing_case_ids=sorted(set(cases)-set(ids)),
        semantic_exact_match=sum(r['semantic_correct'] for r in rows),
        ready_expected=sum(c['gold']['status']=='ready' for c in cases.values()), ready_completed=len(ready),
        ready_gold_plan_success=sum(bool(r.get('independent_gold_audit',{}).get('accepted')) for r in ready),
        ready_end_to_end_strict_success=sum(r['end_to_end_symbolic_success'] for r in ready),
        parsed_contract_search_gold_success=sum(bool(r.get('parsed_semantics_symbolic_search',{}).get('accepted')) for r in ready),
        refusal_expected=sum(c['gold']['status']!='ready' for c in cases.values()), refusal_completed=len(refuse),
        correct_refusals=sum(bool(r.get('refusal_correct')) for r in refuse),
        semantic_misaccept=sum(bool(r.get('semantic_misaccept')) for r in rows),
        errors=[dict(case_id=r['case_id'], error_type=r.get('error_type'), error=r['error']) for r in rows if 'error' in r],
        logged_calls=sum(r['logged_calls'] for r in rows),total_tokens=sum(r['total_tokens'] for r in rows),
        physical_execution=False, development_retest=manifest.get('development_retest_of_v1',False))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory',type=Path)
    a=p.parse_args()
    data=analyze(a.directory)
    (a.directory/'analysis.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
    print(json.dumps(data,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
