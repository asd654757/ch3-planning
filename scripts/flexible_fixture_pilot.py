"""Real model plans and remaining-goal repair in the existing persistent scene."""
import argparse
import json
from pathlib import Path

from ch3.repair.flexible_fixture_planner import FlexibleFixturePlanner
from ch3.vlm.client import DashScopeVLMClient
from scripts.persistent_reference_sequence import run


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--env-file',type=Path,required=True)
    p.add_argument('--seeds',type=int,nargs='+',default=[55,56,57])
    p.add_argument('--update-goals',action='store_true')
    p.add_argument('--compare-feedback',action='store_true',help='Matched direct replan versus error-feedback replan')
    args=p.parse_args()
    if len(args.seeds)!=len(set(args.seeds)) or any(s<0 for s in args.seeds):
        p.error('unique nonnegative seeds required')
    args.output_dir.mkdir(parents=True,exist_ok=False)
    protocol=dict(seeds=args.seeds,task='two_cube_flexible_delivery',
        controlled_goal_update=args.update_goals,goal_update_timing='after_first_supported_delivery',
        update='swap both requested destinations' if args.update_goals else None,
        actual_model=True,shared_initial_candidate=True,episode_resets=1,
        initial_y_spread_m=.025,pick_contact_offset_m=.03,
        backend='fixed_pick_place',max_total_transfers=4,max_repair_calls=2,
        protocol_version='flexible_feedback_v3',compare_feedback=args.compare_feedback,
        shared_acceptance_gate=True,failed_candidate_retry='up to remaining noninitial budget',
        scope='task_update_replanning_pilot_not_natural_execution_failure_recovery')
    (args.output_dir/'protocol.json').write_text(json.dumps(protocol,indent=2))
    client=DashScopeVLMClient(env_path=args.env_file,timeout=60,max_retries=0)
    rows=[]
    with (args.output_dir/'episodes.jsonl').open('x') as f:
        for seed in args.seeds:
            shared={}
            methods=[('MODEL_NO_TASK_REPAIR',False),('MODEL_CONSTRAINT_REPAIR',True)]
            if args.compare_feedback:
                methods=[('MODEL_DIRECT_REPLAN',True),('MODEL_CONSTRAINT_REPAIR',True)]
            if seed%2:methods.reverse()
            for method,recovery in methods:
                print(f'[flexible-fixture] seed={seed} method={method} start',flush=True)
                planner=FlexibleFixturePlanner(client,recovery=recovery,shared=shared,update_goals=args.update_goals,
                    error_feedback=method!='MODEL_DIRECT_REPLAN')
                row=run(seed,args.output_dir/f'seed_{seed}'/method,reobserve_budget=2,model_planner=planner,
                    pick_contact_offset=.03,feedback_v2=True)
                row.update(method=method,scope=protocol['scope'])
                f.write(json.dumps(row)+'\n');f.flush();rows.append(row)
                print(json.dumps(dict(seed=seed,method=method,success=row['success'],model_calls=row['model_calls'],
                    error=row.get('error'))),flush=True)
    summary=dict(completed_episodes=len(rows),actual_model_calls=sum(r['model_calls'] for r in rows),
        methods={m:dict(episodes=sum(r['method']==m for r in rows),successes=sum(r['success'] for r in rows if r['method']==m),
            accepted_remaining_repairs=sum(a.get('phase')=='remaining_task_repair' and a.get('accepted',False)
                for r in rows if r['method']==m for a in r['model_audit'])) for m,_ in methods})
    (args.output_dir/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary),flush=True)
