"""Frozen new wording pilot: rule/model semantics with shared symbolic planner.

One model parsing call per case; model planning only for ready contracts.
Gold never repairs online results. No physical execution or image estimation.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
from ch3.vlm.client import DashScopeVLMClient
from ch3.vlm.task_semantics import (TaskSemantics,parse_semantics,semantic_equal,
    evaluate_plan,deterministic_plan,generate_semantic_remaining_plan)
from ch3.vlm.rule_task_semantics import parse_rule_semantics


def score(contract,plan,gold):
    exact=semantic_equal(contract,gold)
    if contract.status!='ready':
        return dict(semantic_exact=exact,online_decision=contract.status,
            correct_refusal=contract.status==gold.status if gold.status!='ready' else False,
            gold_plan_success=False,strict_success=exact and gold.status!='ready',misaccept=False)
    predicted=evaluate_plan(plan,contract) if plan is not None else {'accepted':False,'reason':'no_plan_within_bound'}
    independent=evaluate_plan(plan,gold) if plan is not None else {'accepted':False,'reason':'no_plan'}
    return dict(semantic_exact=exact,online_decision='plan',predicted_audit=predicted,gold_audit=independent,
        gold_plan_success=bool(independent['accepted']),strict_success=bool(exact and predicted['accepted'] and independent['accepted']),
        misaccept=bool(predicted['accepted'] and not independent['accepted']))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tasks',type=Path,default=Path('data/scenarios/task_semantic_transfer_v1.jsonl'))
    p.add_argument('--env-file',required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--execute',action='store_true')
    a=p.parse_args();a.output_dir.mkdir(parents=True,exist_ok=False)
    rows=[json.loads(line) for line in a.tasks.read_text().splitlines()]
    if len({r['case_id'] for r in rows})!=len(rows) or len({r['instruction'] for r in rows})!=len(rows):
        raise ValueError('duplicate tasks')
    for r in rows:
        gold=TaskSemantics.model_validate(r['gold']);gold.check_scope()
        if gold.status=='ready' and deterministic_plan(gold) is None:raise ValueError('unreachable annotation')
    root=Path(__file__).resolve().parents[1]
    names=['ch3/vlm/task_semantics.py','ch3/vlm/rule_task_semantics.py','scripts/task_semantic_transfer_benchmark.py',str(a.tasks)]
    manifest=dict(protocol='task_semantic_transfer_v1',created_at_utc=datetime.now(timezone.utc).isoformat(),
        commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),
        cases=rows,source_sha256={n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in names},
        phase='new_wording_pilot_not_formal',author_constructed=True,author_knows_rule_grammar=True,
        current_state='fixed_held_blue_fixture',physical_execution=False,visual_state_estimation=False,
        rule_input='same instruction, grounded catalog and held-blue state as model',
        model='qwen-vl-plus',seed=0,temperature=.1,max_tokens=1024,json_mode=False,
        variants=['rule_semantics_search','model_semantics_search','model_semantics_model_plan','oracle_semantics_search'],
        oracle_is_not_natural_language_baseline=True,max_calls=2*len(rows),automatic_reruns=False)
    (a.output_dir/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    if not a.execute:print(json.dumps(dict(executed=False,cases=len(rows))));return
    client=DashScopeVLMClient(env_path=a.env_file,timeout=60,max_retries=0)
    for i,row in enumerate(rows,1):
        print(f"[semantic-transfer] case={i}/{len(rows)} id={row['case_id']} start",flush=True)
        path=a.output_dir/row['case_id'];path.mkdir()
        gold=TaskSemantics.model_validate(row['gold'])
        record=dict(case_id=row['case_id'],subset=row['subset'],family=row['family'],physical_execution=False,results={})
        results=record['results']
        for name,contract in [('rule_semantics_search',parse_rule_semantics(row['instruction'])),('oracle_semantics_search',gold)]:
            try:
                plan=deterministic_plan(contract)
                results[name]=dict(contract=contract.model_dump(),plan=plan,**score(contract,plan,gold))
            except Exception as exc:results[name]=dict(error_type=type(exc).__name__,error=str(exc),strict_success=False)
        try:
            parsed=parse_semantics(client,instruction=row['instruction'],image_path=None,log_path=path/'semantic_call.json',version='v5')
            plan=deterministic_plan(parsed)
            results['model_semantics_search']=dict(contract=parsed.model_dump(),plan=plan,**score(parsed,plan,gold))
            if parsed.status=='ready':
                try:
                    raw=generate_semantic_remaining_plan(client,contract=parsed,image_path=None,log_path=path/'plan_call.json')
                    results['model_semantics_model_plan']=dict(contract=parsed.model_dump(),plan=raw,**score(parsed,raw,gold))
                except Exception as exc:
                    results['model_semantics_model_plan']=dict(semantic_exact=semantic_equal(parsed,gold),error_type=type(exc).__name__,error=str(exc),strict_success=False)
            else:results['model_semantics_model_plan']=dict(contract=parsed.model_dump(),**score(parsed,None,gold))
        except Exception as exc:
            for name in ['model_semantics_search','model_semantics_model_plan']:
                results[name]=dict(error_type=type(exc).__name__,error=str(exc),strict_success=False)
        calls=[json.loads(f.read_text()) for f in path.glob('*_call.json')]
        record.update(logged_calls=len(calls),total_tokens=sum(c['total_tokens'] for c in calls))
        with (a.output_dir/'records.jsonl').open('a') as f:f.write(json.dumps(record,ensure_ascii=False)+'\n')
        print(json.dumps(record,ensure_ascii=False),flush=True)
    (a.output_dir/'summary.json').write_text(json.dumps(dict(completed_at_utc=datetime.now(timezone.utc).isoformat(),completed_cases=len(rows),physical_execution=False),indent=2))
    print(json.dumps(dict(completed_cases=len(rows),output=str(a.output_dir))),flush=True)

if __name__=='__main__':main()
