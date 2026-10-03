"""Development-only replay of observed recovery inputs; never executes physics."""
import argparse
import json
from pathlib import Path

from ch3.repair.flexible_fixture_planner import FlexibleFixturePlanner
from ch3.vlm.client import DashScopeVLMClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--env-file', type=Path, required=True)
    args = parser.parse_args()
    client = DashScopeVLMClient(env_path=args.env_file, timeout=60, max_retries=0)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        for seed in (202, 211):
            folder = args.source / f'seed_{seed}' / 'MODEL_CONSTRAINT_REPAIR'
            source = json.loads((folder / 'summary.json').read_text())
            saved = next(a for a in source['model_audit'] if a['phase'] == 'execution_repair')
            image = folder / 'transfer_1_yellow_place_release_1.png'
            import hashlib
            assert hashlib.sha256(image.read_bytes()).hexdigest() == saved['image_sha256']
            for feedback in (False, True):
                planner = FlexibleFixturePlanner(client, recovery=True, shared={},
                    execution_contract=True, error_feedback=feedback)
                request = saved['request']
                planner.goals = request['goals']
                for item in request['current_task_obligations']:
                    planner.state.at[item['object_id']] = item['observed_location']
                planner.history = [tuple(x) for x in request['executed_history']]
                planner.set_execution_progress(2)
                error = None
                try:
                    planner._remaining_request(image, None)
                    accepted = True
                except ValueError as exc:
                    accepted, error = False, str(exc)
                row = dict(seed=seed, feedback=feedback, accepted=accepted, error=error,
                    scope='saved_observation_planning_only_not_execution_success', audit=planner.audit)
                stream.write(json.dumps(row) + '\n')
                stream.flush()
                print(json.dumps({k:v for k,v in row.items() if k != 'audit'}), flush=True)


if __name__ == '__main__':
    main()
