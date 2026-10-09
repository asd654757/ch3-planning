"""Bounded single-episode supervision smoke; scripted planner by default."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ch3.supervision.metaworld_adapter import build_session
from ch3.supervision.runtime import JsonlJournal, run_session


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--planner', choices=['scripted', 'qwen'], default='scripted')
    p.add_argument('--env-file', type=Path)
    p.add_argument('--model', default='qwen-vl-plus')
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--interrupt-first-grasp', action='store_true')
    p.add_argument('--max-steps', type=int, default=300)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.max_steps < 1:
        p.error('positive step budget required')
    # Reserve journal before environment creation; never overwrite old records.
    with JsonlJournal(args.output) as journal:
        planner = None
        if args.planner == 'qwen':
            from ch3.vlm.client import DashScopeVLMClient
            from ch3.supervision.planner import ClientPlanner
            planner = ClientPlanner(DashScopeVLMClient(model=args.model, env_path=args.env_file), seed=args.seed)
        journal.emit({'event': 'protocol', 'version': 'metaworld_supervision_v1',
            'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'planner_kind': args.planner, 'seed': args.seed,
            'perturbation': 'first_grasp_one_step_timeout' if args.interrupt_first_grasp else 'none',
            'feedback': 'privileged_simulator_structured_state', 'visual_feedback': False,
            'controller': 'fixed_metaworld_controller', 'adflow_weights_used': False,
            'episode_resets': 1, 'max_steps_per_command': args.max_steps})
        session = build_session(planner, seed=args.seed, max_steps=args.max_steps,
                                interrupt_first_grasp=args.interrupt_first_grasp)
        try:
            result = run_session(session, max_observations=12, emit=journal.emit)
            for record in session.backend.results:
                journal.emit({'event': 'primitive_diagnostics', **record})
            result['planner_kind'] = args.planner
            result['api_calls'] = session.supervisor.model_calls if args.planner == 'qwen' else 0
            journal.emit({'event': 'adapter_summary', **{k:v for k,v in result.items() if k != 'event'}})
        finally:
            session.backend.executor.close()
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result['confirmed_complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
