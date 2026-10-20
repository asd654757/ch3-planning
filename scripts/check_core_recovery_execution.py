"""Zero-API physical gate; scripted outputs are NOT evidence of LLM benefit."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from ch3.repair.flexible_fixture_planner import FlexibleFixturePlanner
from scripts.persistent_reference_sequence import run


class ScriptedFixtureClient:
    """Fixed state-based fixture checks, never an experimental planner baseline."""
    def __init__(self, reverse=False):
        self.reverse = reverse

    def complete(self, **kwargs):
        task = json.loads(kwargs['user_prompt'])
        objects = task['objects']
        needed = [o for o in objects if o['location'] != o['goal']]
        # Choose an unoccupied destination first; no simulator state access.
        occupancy = {o['location']: o['id'] for o in objects}
        needed.sort(key=lambda o: (o['goal'] in occupancy and occupancy[o['goal']] != o['id'],
                                   o['id']), reverse=False)
        if self.reverse and len(needed) == 2 and all(o['goal'] not in occupancy for o in needed):
            needed.reverse()
        actions = []
        for o in needed:
            actions.extend([dict(step_id=len(actions)+1, skill='pick', object_id=o['id']),
                dict(step_id=len(actions)+2, skill='place', object_id=o['id'], target_id=o['goal'])])
        return SimpleNamespace(content=json.dumps(dict(actions=actions)), total_tokens=0)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seeds', type=int, nargs='+', default=[780, 781])
    args = p.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    rows = []
    for seed in args.seeds:
        for reverse in (False, True):
            planner = FlexibleFixturePlanner(ScriptedFixtureClient(reverse), recovery=True,
                shared={}, execution_contract=True, compact_feedback=True)
            row = run(seed, args.output_dir/f'{seed}_{int(reverse)}', model_planner=planner,
                feedback_v2=True, execution_recovery=True, pick_contact_offset=.03,
                controlled_place_offset=.045, deviation_transfer_index=0, reobserve_budget=2)
            row['evidence_kind'] = 'scripted_physical_gate_not_model_experiment'
            row['scripted_requests'] = row['model_calls']
            row['actual_api_calls'] = 0
            row['order_reversed'] = reverse
            rows.append(row)
    passed = all(r['success'] and r.get('runtime_episode_reset_count') == 1
                 and any(a['phase'] == 'execution_repair' and a['accepted']
                         for a in r.get('model_audit', [])) for r in rows)
    summary = dict(physical_gate_passed=passed, actual_api_calls=0,
        formal_ready=False, note='Feasibility only; no history-necessity or model advantage claim.',
        episodes=[dict(seed=r['seed'], reverse=r['order_reversed'], success=r['success'],
                       resets=r.get('runtime_episode_reset_count'), error=r.get('error'),
                       initial_sequence=r['reference_sequence']) for r in rows])
    (args.output_dir/'gate.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
