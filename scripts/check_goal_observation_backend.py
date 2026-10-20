"""Zero-API physical compatibility check; not a method-performance experiment."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from ch3.repair.flexible_fixture_planner import FlexibleFixturePlanner
from scripts.persistent_reference_sequence import run


class FixtureClient:
    """Scripted fixture outputs only. Never label these as model-generated plans."""
    def complete(self, **kwargs):
        request = json.loads(kwargs['user_prompt'])
        if request.get('output_schema') == {'decision': 'observe or stop'}:
            return SimpleNamespace(content='{"decision":"observe"}', total_tokens=0)
        actions=[]
        for obj in request['objects']:
            if obj['location'] == obj['goal']:
                continue
            actions.extend([dict(step_id=len(actions)+1, skill='pick', object_id=obj['id']),
                dict(step_id=len(actions)+2, skill='place', object_id=obj['id'],target_id=obj['goal'])])
        return SimpleNamespace(content=json.dumps({'actions':actions}),total_tokens=0)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--physical-screen',action='store_true')
    parser.add_argument('--observation-camera',choices=['corner2','topview','corner'],default='corner2')
    parser.add_argument('--goal-budgets',type=int,nargs='+',choices=[0,1],default=[1])
    parser.add_argument('--seeds',type=int,nargs='+',default=[700,701])
    args=parser.parse_args()
    if len(args.seeds)!=len(set(args.seeds)) or len(args.goal_budgets)!=len(set(args.goal_budgets)):
        parser.error('Seeds and budgets must be unique')
    args.output.mkdir(parents=True,exist_ok=False)
    rows=[]
    for seed in args.seeds:
        for budget in args.goal_budgets:
            planner=FlexibleFixturePlanner(FixtureClient(),recovery=True,shared={},
                execution_contract=True,compact_feedback=True,execution_diagnostics=True)
            result=run(seed,args.output/f'seed_{seed}_budget_{budget}',model_planner=planner,
                reobserve_budget=2,pick_contact_offset=.03,feedback_v2=True,
                execution_recovery=True,controlled_place_offset=.045,goal_reobserve_budget=budget,
                physical_screen=args.physical_screen,observation_camera=args.observation_camera)
            row=dict(seed=seed,goal_reobserve_budget=budget,success=result['success'],error=result.get('error'),
                evidence_kind='physical_simulation_with_scripted_fixture_plans',actual_api_calls=0,
                episode_resets=result.get('episode_resets'),
                initial_unknown_checks=sum(e.get('observed_goal',{}).get('status')=='unknown' for e in result['events']),
                unknown_decisions=sum(e.get('event')=='goal_observation_decision' for e in result['events']),
                extra_observations=sum(e.get('event')=='goal_reobservation_result' for e in result['events']))
            rows.append(row)
            print(json.dumps(row),flush=True)
    summary=dict(episodes=rows,actual_api_calls=0,
        observation_branch_exercised=any(r['extra_observations'] for r in rows),
        limitation='No observed UNKNOWN means compatibility only, not validation of acquisition benefit.')
    (args.output/'check_summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary),flush=True)


if __name__=='__main__':
    main()
