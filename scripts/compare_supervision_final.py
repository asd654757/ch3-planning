"""Small matched comparison. Default requires real Qwen; scripted is wiring-only."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ch3.supervision.comparison import SharedInitialPlanner
from ch3.supervision.final_variants import WithoutConflictSupervisor, StateFirstDirectSupervisor
from ch3.supervision.core import Supervisor
from ch3.supervision.metaworld_adapter import build_session, ScriptedSmokePlanner
from ch3.supervision.runtime import Session, JsonlJournal, run_session

METHODS = {'STATE_FIRST_DIRECT': StateFirstDirectSupervisor,
           'WITHOUT_CONFLICT': WithoutConflictSupervisor, 'SUPERVISED_RECOVERY': Supervisor}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True, help='Frozen formal slip journal')
    p.add_argument('--seeds', type=int, default=10)
    p.add_argument('--start-seed', type=int, default=0)
    p.add_argument('--planner', choices=['qwen', 'scripted'], default='qwen')
    p.add_argument('--perturbation', choices=['grasp_timeout', 'place_timeout', 'post_grasp_slip'], default='grasp_timeout')
    p.add_argument('--model', default='qwen-vl-plus')
    p.add_argument('--env-file', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.seeds < 1 or args.start_seed < 0:
        p.error('positive seed count and nonnegative start seed required')
    source_events = [json.loads(line) for line in args.source.read_text().splitlines()]
    protocols = [e for e in source_events if e['event'] == 'comparison_protocol']
    if len(protocols) != 1 or protocols[0]['perturbation'] != args.perturbation:
        p.error('source must match perturbation')
    initial_outputs = {e['seed']: e['output'] for e in source_events if e['event'] == 'shared_initial_plan'}
    physical_states = {e['seed']: e['state'] for e in source_events
                       if e['event'] == 'initial_physical_state' and e['method'] == 'NO_RECOVERY'}
    seeds = range(args.start_seed, args.start_seed + args.seeds)
    if any(seed not in initial_outputs or seed not in physical_states for seed in seeds):
        p.error('source lacks requested paired seeds')
    import hashlib
    source_hash = hashlib.sha256(args.source.read_bytes()).hexdigest()
    client = None
    if args.planner == 'qwen':
        from ch3.vlm.client import DashScopeVLMClient
        from ch3.supervision.planner import ClientPlanner
        client = DashScopeVLMClient(model=args.model, env_path=args.env_file, timeout=60, max_retries=0)
    rows = []
    with JsonlJournal(args.output) as journal:
        journal.emit({'event': 'comparison_protocol', 'version': 'supervision_final_attribution_v1',
            'source': str(args.source), 'source_sha256': source_hash,
            'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'planner_kind': args.planner, 'model': args.model if client else None,
            'seed_count': args.seeds, 'start_seed': args.start_seed, 'methods': list(METHODS),
            'feedback_version': 'current_state_authority_and_conflict_v1',
            'attempts_per_preparation': 2,
            'shared_initial_plan': True, 'feedback': 'privileged_simulator_state',
            'perturbation': args.perturbation, 'visual_feedback': False,
            'shared_safety_validation': True, 'max_model_attempts': 6,
            'max_commands': 8, 'max_observations': 12,
            'initial_plan_cost': 'replayed from frozen journal for all methods; logical count includes initial plan; actual API usage is recovery only'})
        for seed in range(args.start_seed, args.start_seed + args.seeds):
            initial = initial_outputs[seed]
            initial_physical = physical_states[seed]
            for method, cls in METHODS.items():
                delegate = ClientPlanner(client, seed=seed) if client else ScriptedSmokePlanner()
                planner = SharedInitialPlanner(delegate, initial)
                session = build_session(planner, seed=seed, interrupt_first_grasp=args.perturbation == "grasp_timeout",
                                        perturbation=args.perturbation)
                try:
                    raw = session.backend.executor._state()
                    physical = {k: raw[k].tolist() for k in ('puck_pos', 'hand_pos', 'target_pos')}
                    physical['gripper_distance'] = raw['gripper_distance']
                    if initial_physical is None:
                        initial_physical = physical
                    if physical != initial_physical:
                        raise RuntimeError('paired initial physical states differ')
                    old = session.supervisor
                    supervisor = cls(old.task, old.validator, planner,
                                     max_model_calls=6, max_commands=8)
                    session = Session(supervisor, session.observer, session.backend, 'simulator')
                    print(f'[supervision-compare] seed={seed} method={method} start', flush=True)
                    def emit(e):
                        journal.emit({**e, 'seed': seed, 'method': method})
                    emit({'event': 'initial_physical_state', 'state': physical})
                    result = run_session(session, max_observations=12, emit=emit)
                    if initial is None:
                        initial = planner.generated_initial
                        if initial is None:
                            raise RuntimeError('initial model request failed; cannot construct matched comparison')
                        journal.emit({'event': 'shared_initial_plan', 'seed': seed, 'output': initial})
                    row = {**result, 'seed': seed, 'method': method,
                           'actual_api_calls': planner.actual_calls if client else 0,
                           'planner_kind': args.planner,
                           'model_usage': getattr(delegate, 'usage', []),
                           'perturbation': args.perturbation,
                           'perturbation_events': session.backend.perturbation_events,
                           'perturbation_executed': (any(r['primitive'] == 'grasp' and r['steps'] == 1
                                                       for r in session.backend.results) if args.perturbation == 'grasp_timeout'
                               else any(e['realized'] for e in session.backend.perturbation_events)),
                           'rejected_candidates': sum(not e['accepted'] for e in supervisor.audit)}
                    repair_attempts = [e for e in supervisor.audit
                                       if e['request']['observation_sequence'] > 0]
                    row['first_repair_accepted'] = (repair_attempts[0]['accepted']
                                                    if repair_attempts else None)
                    row['rejection_reasons'] = [e.get('rejection') for e in supervisor.audit
                                                if not e['accepted']]
                    for r in session.backend.results:
                        emit({'event': 'primitive_diagnostics', **r})
                    emit({**row, 'event': 'case_summary'})
                    rows.append(row)
                    print(f'[supervision-compare] seed={seed} method={method} complete={result["confirmed_complete"]}', flush=True)
                finally:
                    session.backend.executor.close()
        summary = {'event': 'comparison_summary', 'completed_at_utc': datetime.now(timezone.utc).isoformat(),
                   'planner_kind': args.planner, 'completed_cases': len(rows),
                   'actual_api_calls': sum(r['actual_api_calls'] for r in rows),
                   'by_method': {m: {'cases': sum(r['method'] == m for r in rows),
                       'successes': sum(r['method'] == m and r['confirmed_complete'] for r in rows),
                       'logical_planner_calls': sum(r['model_calls'] for r in rows if r['method'] == m)}
                       for m in METHODS}}
        journal.emit(summary)
        print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
