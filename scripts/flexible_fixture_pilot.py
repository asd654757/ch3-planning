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
    p.add_argument('--execution-deviation',action='store_true',help='First place target offset; RGB-triggered off-goal recovery')
    p.add_argument('--include-no-repair',action='store_true',help='Also retain a safe-stop reference in feedback comparison')
    p.add_argument('--difficulty',choices=['medium','hard'],help='Second delivery deviation; hard also updates goals after deviation')
    p.add_argument('--repair-contract-v7',action='store_true',help='Typed execution feedback, occupancy prerequisites and episode remaining-transfer gate')
    args=p.parse_args()
    if args.difficulty:
        args.execution_deviation=True
        args.compare_feedback=True
    if args.execution_deviation and args.update_goals:
        p.error('separate execution-deviation and goal-update conditions')
    if args.include_no_repair and not args.compare_feedback:
        p.error('--include-no-repair requires --compare-feedback')
    if len(args.seeds)!=len(set(args.seeds)) or any(s<0 for s in args.seeds):
        p.error('unique nonnegative seeds required')
    args.output_dir.mkdir(parents=True,exist_ok=False)
    protocol=dict(seeds=args.seeds,task='two_cube_flexible_delivery',
        controlled_goal_update=args.update_goals or args.difficulty=='hard',
        goal_update_timing=('after_supported_second_delivery_deviation' if args.difficulty=='hard'
            else 'after_first_supported_delivery' if args.update_goals else None),
        update='swap both requested destinations' if args.update_goals or args.difficulty=='hard' else None,
        actual_model=True,shared_initial_candidate=True,episode_resets=1,
        initial_y_spread_m=.025,pick_contact_offset_m=.03,
        backend='fixed_pick_place',max_total_transfers=4,max_repair_calls=2,
        protocol_version='flexible_feedback_v7_2' if args.repair_contract_v7 else 'flexible_feedback_v6',compare_feedback=args.compare_feedback,
        execution_contract_v7=args.repair_contract_v7,
        repair_semantics='complete_replacement_not_incremental_patch_rejected_plans_not_executed',
        goal_feedback='diagnostic_category_and_count_no_candidate_or_predicted_state',
        remaining_goals_available_to_both=True,
        shared_acceptance_gate=True,failed_candidate_retry='up to remaining noninitial budget',
        controlled_place_offset_m=.045 if args.execution_deviation else 0.,
        difficulty=args.difficulty,deviation_transfer_index=1 if args.difficulty else 0,
        update_on_deviation=args.difficulty=='hard',
        scope=('controlled_off_goal_execution_recovery_not_natural_failure' if args.execution_deviation
            else 'task_update_replanning_pilot_not_natural_execution_failure_recovery'))
    (args.output_dir/'protocol.json').write_text(json.dumps(protocol,indent=2))
    client=DashScopeVLMClient(env_path=args.env_file,timeout=60,max_retries=0)
    rows=[]
    with (args.output_dir/'episodes.jsonl').open('x') as f:
        for seed in args.seeds:
            shared={}
            methods=[('MODEL_NO_TASK_REPAIR',False),('MODEL_CONSTRAINT_REPAIR',True)]
            if args.compare_feedback:
                methods=[('MODEL_DIRECT_REPLAN',True),('MODEL_CONSTRAINT_REPAIR',True)]
                if args.include_no_repair:
                    methods.append(('MODEL_NO_EXECUTION_REPAIR' if args.execution_deviation
                        else 'MODEL_NO_TASK_REPAIR',False))
            if seed%2:methods.reverse()
            for method,recovery in methods:
                print(f'[flexible-fixture] seed={seed} method={method} start',flush=True)
                planner=FlexibleFixturePlanner(client,recovery=recovery,shared=shared,update_goals=args.update_goals,
                    error_feedback=method!='MODEL_DIRECT_REPLAN',update_on_deviation=args.difficulty=='hard',
                    execution_contract=args.repair_contract_v7)
                row=run(seed,args.output_dir/f'seed_{seed}'/method,reobserve_budget=2,model_planner=planner,
                    pick_contact_offset=.03,feedback_v2=True,execution_recovery=args.execution_deviation,
                    controlled_place_offset=.045 if args.execution_deviation else 0.,
                    deviation_transfer_index=1 if args.difficulty else 0)
                row.update(method=method,scope=protocol['scope'])
                f.write(json.dumps(row)+'\n');f.flush();rows.append(row)
                print(json.dumps(dict(seed=seed,method=method,success=row['success'],model_calls=row['model_calls'],
                    error=row.get('error'))),flush=True)
    summary=dict(completed_episodes=len(rows),actual_model_calls=sum(r['model_calls'] for r in rows),
        methods={m:dict(episodes=sum(r['method']==m for r in rows),successes=sum(r['success'] for r in rows if r['method']==m),
            accepted_remaining_repairs=sum(a.get('phase')=='remaining_task_repair' and a.get('accepted',False)
                for r in rows if r['method']==m for a in r['model_audit']),
            accepted_execution_repairs=sum(a.get('phase')=='execution_repair' and a.get('accepted',False)
                for r in rows if r['method']==m for a in r['model_audit'])) for m,_ in methods})
    (args.output_dir/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary),flush=True)
