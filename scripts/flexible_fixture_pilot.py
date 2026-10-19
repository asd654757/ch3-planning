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
    p.add_argument('--model',default='deepseek-v4.1-flash',
        help='OpenAI-compatible model used for live planning/recovery (recorded in protocol)')
    p.add_argument('--seeds',type=int,nargs='+',default=[55,56,57])
    p.add_argument('--max-tokens',type=int,default=4096)
    p.add_argument('--disable-thinking',action='store_true',
        help='Explicitly disable provider thinking for both methods; changes inference protocol')
    p.add_argument('--execution-diagnostics',action='store_true',
        help='v9 ablation: retain observed execution diagnostic separately from rejected candidates')
    p.add_argument('--compare-compact-direct',action='store_true',
        help='Preferred comparison: direct replanning versus compact feedback only; excludes legacy')
    p.add_argument('--compare-feedback-formats',action='store_true',
        help='Matched direct, legacy diagnostic, compact diagnostic; shared initial candidate')
    p.add_argument('--update-goals',action='store_true')
    p.add_argument('--compare-feedback',action='store_true',help='Matched direct replan versus error-feedback replan')
    p.add_argument('--execution-deviation',action='store_true',help='First place target offset; RGB-triggered off-goal recovery')
    p.add_argument('--include-no-repair',action='store_true',help='Also retain a safe-stop reference in feedback comparison')
    p.add_argument('--difficulty',choices=['medium','hard'],help='Second delivery deviation; hard also updates goals after deviation')
    p.add_argument('--execution-contract', '--repair-contract-v7', dest='repair_contract_v7',
        action='store_true',help='Versioned execution contract; current protocol v8 (legacy flag is an alias)')
    args=p.parse_args()
    if args.max_tokens <= 0:
        p.error('--max-tokens must be positive')
    if args.execution_diagnostics and not args.repair_contract_v7:
        p.error('--execution-diagnostics requires --execution-contract')
    if args.compare_compact_direct and args.compare_feedback_formats:
        p.error('compact/direct comparison cannot include legacy formats')
    if args.compare_compact_direct and args.include_no_repair:
        p.error('compact/direct comparison contains exactly two methods')
    if args.compare_feedback_formats or args.compare_compact_direct:
        if not args.repair_contract_v7:
            p.error('feedback format comparisons require --execution-contract')
        args.execution_diagnostics = True
        args.compare_feedback = True
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
        actual_model=True,model=args.model,max_tokens=args.max_tokens,
        enable_thinking=False if args.disable_thinking else None,
        shared_initial_candidate=True,episode_resets=1,
        initial_y_spread_m=.025,pick_contact_offset_m=.03,
        backend='fixed_pick_place',max_total_transfers=4,max_repair_calls=2,
        protocol_version='flexible_feedback_v8' if args.repair_contract_v7 else 'flexible_feedback_v6',compare_feedback=args.compare_feedback,
        execution_contract_v7=args.repair_contract_v7,
        repair_semantics='complete_replacement_not_incremental_patch_rejected_plans_not_executed',
        goal_feedback=('isolated_unexecuted_candidate_and_violations_no_predicted_state'
            if args.repair_contract_v7 else 'diagnostic_category_and_count_no_candidate_or_predicted_state'),
        remaining_goals_available_to_both=True,
        shared_acceptance_gate=True,failed_candidate_retry='up to remaining noninitial budget',
        controlled_place_offset_m=.045 if args.execution_deviation else 0.,
        difficulty=args.difficulty,deviation_transfer_index=1 if args.difficulty else 0,
        update_on_deviation=args.difficulty=='hard',
        scope=('controlled_off_goal_execution_recovery_not_natural_failure' if args.execution_deviation
            else 'task_update_replanning_pilot_not_natural_execution_failure_recovery'))
    protocol['execution_diagnostics'] = args.execution_diagnostics
    if args.execution_diagnostics:
        protocol['protocol_version'] = 'flexible_feedback_v9_diagnostic_ablation'
    if args.compare_feedback_formats:
        protocol['protocol_version'] = 'feedback_format_v10_development'
        protocol['method_order'] = 'cyclic_by_seed_modulo_three'
    if args.compare_compact_direct:
        protocol['protocol_version'] = 'compact_direct_v11_accurate_state_control'
        protocol['method_order'] = 'alternating_by_seed_parity'
        protocol['legacy_feedback_excluded'] = True
        protocol['scope'] += '; accurate-state control only, not history-dependent recovery evidence'
    (args.output_dir/'protocol.json').write_text(json.dumps(protocol,indent=2))
    client=DashScopeVLMClient(env_path=args.env_file,model=args.model,timeout=60,max_retries=0)
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
            if args.compare_feedback_formats:
                methods=[('MODEL_DIRECT_REPLAN',True),('MODEL_CONSTRAINT_REPAIR',True),
                    ('MODEL_COMPACT_REPAIR',True)]
                shift=seed%3
                methods=methods[shift:]+methods[:shift]
            if args.compare_compact_direct:
                methods=[('MODEL_DIRECT_REPLAN',True),('MODEL_COMPACT_REPAIR',True)]
                if seed%2:
                    methods.reverse()
            for method,recovery in methods:
                print(f'[flexible-fixture] seed={seed} method={method} start',flush=True)
                planner=FlexibleFixturePlanner(client,recovery=recovery,shared=shared,update_goals=args.update_goals,
                    error_feedback=method!='MODEL_DIRECT_REPLAN',update_on_deviation=args.difficulty=='hard',
                    execution_contract=args.repair_contract_v7,max_tokens=args.max_tokens,
                    enable_thinking=False if args.disable_thinking else None,
                    execution_diagnostics=args.execution_diagnostics,
                    compact_feedback=method=='MODEL_COMPACT_REPAIR')
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
