"""Actual VLM initial plans plus bounded diagnostic repair, same shared candidate."""
import argparse
import json
from pathlib import Path
from ch3.vlm.client import DashScopeVLMClient
from ch3.repair.sequence_model_planner import SequenceModelPlanner
from scripts.persistent_reference_sequence import run


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--env-file', type=Path, required=True)
    p.add_argument('--seeds', type=int, default=3)
    p.add_argument('--seed-start', type=int, default=0)
    p.add_argument('--long-task', action='store_true')
    p.add_argument('--execution-recovery', action='store_true')
    p.add_argument('--pick-contact-offset', type=float, default=.015)
    args = p.parse_args()
    if args.seeds < 1 or args.seed_start < 0:
        p.error('positive seeds and nonnegative seed-start required')
    if not .015 <= args.pick_contact_offset <= .04:
        p.error('pick-contact-offset must be between .015 and .04 meters')
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / 'protocol.json').write_text(json.dumps(dict(
        seeds=list(range(args.seed_start, args.seed_start+args.seeds)), long_task=args.long_task,
        injected_faults=0, shared_initial_candidate=True, reobserve_budget=2,
        model='client_configured_model', instruction_source='fixed_natural_language_task',
        actual_execution=True, execution_recovery=args.execution_recovery,
        pick_contact_offset_m=args.pick_contact_offset), indent=2))
    client = DashScopeVLMClient(env_path=args.env_file, timeout=60, max_retries=0)
    rows = []
    with (args.output_dir / 'episodes.jsonl').open('x') as f:
        for seed in range(args.seed_start, args.seed_start+args.seeds):
            shared = {}
            methods = [('MODEL_NO_PLAN_REPAIR', False), ('MODEL_DIAGNOSTIC_REPAIR', True)]
            if seed % 2:
                methods.reverse()
            for method, recovery in methods:
                print(f'[model-sequence] seed={seed} method={method} start', flush=True)
                planner = SequenceModelPlanner(client, recovery=recovery, shared=shared, long_task=args.long_task)
                row = run(seed, args.output_dir / f'seed_{seed}' / method,
                          reobserve_budget=2, model_planner=planner, execution_recovery=args.execution_recovery,
                          pick_contact_offset=args.pick_contact_offset)
                row['method'] = method
                f.write(json.dumps(row)+'\n'); f.flush(); rows.append(row)
                print(json.dumps(dict(seed=seed, method=method, success=row['success'], model_calls=row['model_calls'],
                                     failure_stage=row.get('failure_stage'))), flush=True)
    summary = dict(completed_episodes=len(rows), actual_model_calls=sum(r['model_calls'] for r in rows),
        scope=('live_model_initial_planning_and_execution_feedback_repair_not_full_routed'
               if args.execution_recovery else 'live_model_initial_planning_and_plan_rejection_repair_not_full_routed'),
        methods={m: dict(episodes=sum(r['method']==m for r in rows), successes=sum(r['success'] for r in rows if r['method']==m),
            repair_calls=sum(sum(a['request']['rejection_feedback'] is not None or a.get('phase') == 'execution_repair'
                                 for a in r['model_audit']) for r in rows if r['method']==m),
            execution_repair_calls=sum(sum(a.get('phase') == 'execution_repair' for a in r['model_audit'])
                                       for r in rows if r['method']==m))
            for m, _ in methods})
    (args.output_dir / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)
