"""Real-model correction of a saved rejected candidate; NOT an execution benchmark."""
import argparse
import hashlib
import json
from pathlib import Path

from ch3.repair.flexible_fixture_planner import FlexibleFixturePlanner, evaluate_flexible
from ch3.repair.persistent_model_recovery import strict_plan
from ch3.vlm.client import DashScopeVLMClient


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-dir',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--env-file',type=Path,required=True)
    args=p.parse_args()
    saved=json.loads((args.source_dir/'summary.json').read_text())
    # Use only the original online model request/candidate, never terminal scores.
    failed=next(a for a in saved['model_audit'] if a['phase']=='remaining_task_repair' and not a['accepted'])
    image=next((p for p in sorted(args.source_dir.glob('observed_release_*.png'))
        if hashlib.sha256(p.read_bytes()).hexdigest()==failed['image_sha256']),None)
    if image is None:
        raise ValueError('saved_online_suffix_image_required')
    client=DashScopeVLMClient(env_path=args.env_file,timeout=60,max_retries=0)
    rows=[]
    for enabled in (False,True):
        planner=FlexibleFixturePlanner(client,recovery=True,shared={},update_goals=True,error_feedback=enabled)
        for obj,region in failed['request']['executed_history']:
            planner.observe_release(obj,region)
        if planner.goals!=failed['request']['goals']:
            raise ValueError('saved_goal_mismatch')
        candidate=strict_plan(failed['content'],fixed_right_arm=True)
        _,rejection=evaluate_flexible(candidate,planner.state,planner.goals,planner.protected())
        if rejection is None:
            raise ValueError('diagnostic_candidate_must_be_rejected')
        rejection['rejected_candidate']=candidate.model_dump(mode='json')
        try:
            executable=planner._remaining_request(image,rejection)
            accepted=executable is not None
        except ValueError:
            accepted=False
        rows.append(dict(method='MODEL_CONSTRAINT_REPAIR' if enabled else 'MODEL_DIRECT_REPLAN',
            accepted=accepted,audit=planner.audit))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(dict(scope='selected_failure_replay_not_performance_or_execution_evidence',
        source=str(args.source_dir),rows=rows),indent=2))
    print(json.dumps(dict(scope='selected_failure_replay',results=[dict(method=r['method'],accepted=r['accepted'],
        calls=len(r['audit'])) for r in rows])),flush=True)


if __name__=='__main__':
    main()
