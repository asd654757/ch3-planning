"""Frozen held-out replication and automatic closeout, with failure-inclusive output."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone


def main():
    root = Path(sys.argv[1])
    files = sorted(set(p for base in ('ch3', 'scripts') for p in Path(base).rglob('*.py')))
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    manifest = dict(protocol='shared_recovery_evidence_v1_frozen_closure', seeds=list(range(800,850)),
        conditions=['medium','hard'], expected_episodes=200, model='deepseek-v4.1-flash',
        max_tokens=4096, enable_thinking=False, source_sha256=hashes,
        git_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        rules=['No outcome-based seed exclusion, prompt/score changes, or posthoc sample expansion.',
               'Same raw evidence, validators, fixed controller, scoring, noninitial budget; alternating order.',
               'Shared initial candidate; report recovery costs separately from actual overall API costs.',
               'API/parse/physical/terminal failures retained. Null and negative results retained.',
               'Single known fixture family; seeds are layouts, not independent task templates.',
               'Mechanism ablation is a separate interface test, not physical performance evidence.'])
    (root/'pilot_manifest.json').write_text(json.dumps(manifest,indent=2))
    print('[closure] FROZEN '+str(root),flush=True)
    env = dict(os.environ, MUJOCO_GL='egl', PYTHONPATH=str(Path.cwd()))
    codes = {}
    for condition in manifest['conditions']:
        cmd = [sys.executable,'-u','scripts/flexible_fixture_pilot.py',
            '--env-file',str(Path.home()/'.config/ch3-planning/dashscope.env'),
            '--disable-thinking','--max-tokens','4096','--difficulty',condition,
            '--execution-contract','--compare-compact-direct', '--output-dir',str(root/condition),
            '--seeds', *map(str,manifest['seeds'])]
        codes[condition] = subprocess.call(cmd,env=env)
        print(f'[closure] condition={condition} PROCESS_EXIT={codes[condition]}',flush=True)
    changed = [p for p,h in hashes.items() if not Path(p).exists() or hashlib.sha256(Path(p).read_bytes()).hexdigest()!=h]
    from scripts.validate_recovery_closure import closeout
    result = closeout(root,root/'closeout')
    result.update(process_exit_codes=codes,changed_source_files=changed,
        completed_at_utc=datetime.now(timezone.utc).isoformat())
    result['closure_complete'] = result['data_complete'] and result['isolation_passed'] and not changed and not any(codes.values())
    (root/'completion.json').write_text(json.dumps(result,indent=2))
    print('[closure] '+json.dumps(result),flush=True)
    return 0 if result['closure_complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
