"""Paired natural two-object execution; observation recovery, NOT model repair."""
import argparse
from collections import Counter
import json
from pathlib import Path

from scripts.persistent_reference_sequence import run


def summarize(rows, seeds):
    methods = ('STOP_ON_FAILURE', 'BOUNDED_REOBSERVE')
    index = {(r['seed'], r['method']): r for r in rows}
    expected = {(s, m) for s in seeds for m in methods}
    complete = len(index) == len(rows) and set(index) == expected
    groups = {}
    for method in methods:
        selected = [r for r in rows if r['method'] == method]
        groups[method] = dict(episodes=len(selected), successes=sum(r['success'] for r in selected),
            failure_stages=dict(Counter(r.get('failure_stage', 'terminal_score') for r in selected if not r['success'])),
            extra_observation_attempts=sum(sum(o['round'] > 0 for o in r['destination_observations']) for r in selected),
            model_calls=sum(r['model_calls'] for r in selected))
    wins = losses = ties = 0
    for seed in seeds:
        a = index.get((seed, methods[0])); b = index.get((seed, methods[1]))
        if a is None or b is None:
            continue
        wins += b['success'] and not a['success']
        losses += a['success'] and not b['success']
        ties += a['success'] == b['success']
    return dict(scope='natural_execution_observation_recovery_not_routed_or_model_repair',
                complete=complete, expected_episodes=len(expected), completed_episodes=len(rows),
                summary_by_method=groups, paired_wins=wins, paired_losses=losses, paired_ties=ties,
                injected_faults=0, outcome_selection=False)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seeds', type=int, default=30)
    args = p.parse_args()
    if args.seeds < 1:
        p.error('positive seeds required')
    args.output_dir.mkdir(parents=True, exist_ok=False)
    seeds = list(range(args.seeds))
    (args.output_dir / 'protocol.json').write_text(json.dumps(dict(
        seeds=seeds, methods={'STOP_ON_FAILURE': 0, 'BOUNDED_REOBSERVE': 2},
        episode_step_limit=1000, injected_faults=0, fixed_controller=True,
        model_calls=0, initial_state='controlled_known_color_scene',
        sequence=['blue_to_return_region', 'yellow_to_green_region'],
        scoring='existing_single_snapshot_and_discrete_protected_displacement_not_stability_test'), indent=2))
    rows = []
    with (args.output_dir / 'episodes.jsonl').open('x') as f:
        for seed in seeds:
            methods = [('STOP_ON_FAILURE', 0), ('BOUNDED_REOBSERVE', 2)]
            if seed % 2:
                methods.reverse()
            for method, budget in methods:
                print(f'[natural-compare] seed={seed} method={method} start', flush=True)
                row = run(seed, args.output_dir / f'seed_{seed}' / method, reobserve_budget=budget)
                row['method'] = method
                rows.append(row)
                f.write(json.dumps(row) + '\n'); f.flush()
                print(f"[natural-compare] seed={seed} method={method} success={row['success']}", flush=True)
    summary = summarize(rows, seeds)
    (args.output_dir / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
