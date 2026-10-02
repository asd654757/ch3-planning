"""Check protocol-integrity gates, then optionally launch frozen formal batch.

No success-rate threshold, no selecting easier seeds, no automatic reruns.
This is a limited task-switch experiment, not proof of general ROUTED efficacy.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def audit(pilot):
    summary = json.loads((pilot / 'summary.json').read_text())
    manifest = json.loads((pilot / 'manifest.json').read_text())
    rows = summary['records']
    errors = []
    if len(rows) != len(manifest['cases']) or {r['case_id'] for r in rows} != {r['case_id'] for r in manifest['cases']}:
        errors.append('missing_or_duplicate_cases')
    for row in rows:
        if row.get('infrastructure_error') or row.get('exit_code') != 0:
            errors.append(row['case_id'] + ':infrastructure_error')
            continue
        if row.get('episode_resets') != 1 or row.get('recovery_episode_resets') != 0:
            errors.append(row['case_id'] + ':episode_reset_contract')
        if row['blackout_destination'] and row.get('yellow_placement_attempted'):
            errors.append(row['case_id'] + ':placement_during_missing_feedback')
        data = json.loads((pilot / row['case_id'] / 'summary.json').read_text())
        skills = [e['stage'] for e in data.get('events', [])]
        if skills != ['blue_pick', 'blue_return', 'yellow_pick', 'yellow_place'][:len(skills)]:
            errors.append(row['case_id'] + ':unexpected_or_repeated_execution')
        if len(data.get('reobservations', [])) > row['reobserve_budget'] + 1:
            errors.append(row['case_id'] + ':observation_budget')
    root = Path(__file__).resolve().parents[1]
    import hashlib
    for name, expected in manifest['code_sha256'].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            errors.append('protocol_code_changed:' + name)
    return {'ready_for_limited_task_switch_formal': not errors, 'errors': errors,
            'completed_cases': len(rows), 'success_rate_used_as_gate': False,
            'scope': 'protocol_integrity_not_general_method_readiness'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot', type=Path, required=True)
    parser.add_argument('--env-file', required=True)
    parser.add_argument('--formal-output', type=Path, required=True)
    parser.add_argument('--formal-log', type=Path, required=True)
    parser.add_argument('--launch-on-pass', action='store_true')
    parser.add_argument('--wait-seconds', type=int, default=0)
    args = parser.parse_args()
    deadline = time.monotonic() + args.wait_seconds
    while not (args.pilot / 'summary.json').exists() and time.monotonic() < deadline:
        time.sleep(5)
    if not (args.pilot / 'summary.json').exists():
        result = {'ready_for_limited_task_switch_formal': False, 'errors': ['pilot_incomplete']}
    else:
        result = audit(args.pilot)
    result['checked_at_utc'] = datetime.now(timezone.utc).isoformat()
    if result['ready_for_limited_task_switch_formal'] and args.launch_on_pass:
        if args.formal_output.exists() or args.formal_log.exists():
            raise ValueError('refuse overwrite formal output/log')
        with args.formal_log.open('x') as stream:
            process = subprocess.Popen([sys.executable, '-u', str(Path(__file__).with_name('run_task_switch_benchmark.py')),
                '--output-dir', str(args.formal_output), '--env-file', args.env_file, '--seeds', '30', '--phase', 'formal', '--execute'],
                stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
                env=dict(os.environ, PYTHONPATH='.', MUJOCO_GL='egl'))
        for name, value in [('pid', str(process.pid)), ('log', str(args.formal_log)), ('output', str(args.formal_output))]:
            Path('/tmp/task_switch_observation_formal_' + name).write_text(value + '\n')
        result['formal_pid'] = process.pid
        result['formal_output'] = str(args.formal_output)
        result['formal_task_runs'] = 150
    (args.pilot / 'scale_gate.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
