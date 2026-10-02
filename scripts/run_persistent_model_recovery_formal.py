"""Launch the frozen bounded formal protocol; no selection by pilot success."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    config=json.loads(Path('config/benchmarks/persistent_model_recovery_v1.json').read_text())
    if args.output.exists():parser.error('output already exists; do not overwrite records')
    env=dict(os.environ,PYTHONPATH='.',MUJOCO_GL='egl')
    subprocess.run([sys.executable,'-u','scripts/sim_persistent_recovery.py',
        '--seeds',str(config['seeds']),'--budget',str(config['budget_per_primitive']),
        '--perturbations',*config['perturbations'],'--methods',*config['methods'],
        '--supported-table-goal','--phased-grasp','--env-file',str(args.env_file),
        '--output',str(args.output)],env=env,check=True)
    subprocess.run([sys.executable,'scripts/analyze_persistent_model_recovery.py',
        '--source',str(args.output),'--seeds',str(config['seeds'])],check=True)
