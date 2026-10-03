"""Predeclared pilot feasibility gate then separate 30-seed natural model batch."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--env-file', type=Path, required=True)
    args = p.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    def execute(name, count, start):
        out = args.output_dir / name
        subprocess.run([sys.executable, '-u', 'scripts/model_sequence_pilot.py', '--long-task',
            '--output-dir', str(out), '--env-file', str(args.env_file),
            '--seeds', str(count), '--seed-start', str(start)], check=True)
        return [json.loads(l) for l in (out/'episodes.jsonl').read_text().splitlines()]
    rows = execute('pilot', 3, 0)
    completed_seeds = {r['seed'] for r in rows if r['success']}
    mismatch = any('shared_initial_input_mismatch' in str(a) for r in rows for a in r['model_audit'])
    gate = dict(pilot_completed_seeds=sorted(completed_seeds), gate_passed=len(completed_seeds)>=2 and not mismatch,
                formal_seeds=list(range(3,33)), status='pilot_done')
    (args.output_dir/'gate.json').write_text(json.dumps(gate, indent=2))
    if gate['gate_passed']:
        print('[model-long] feasibility gate passed; starting separate seeds 3..32', flush=True)
        execute('expanded', 30, 3)
        gate['status'] = 'expanded_complete'
    else:
        gate['status'] = 'stopped_at_feasibility_gate'
        print('[model-long] feasibility gate failed; do not expand', flush=True)
    (args.output_dir/'gate.json').write_text(json.dumps(gate, indent=2))
