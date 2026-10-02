"""Frozen symbolic difficulty pilot, NOT physical or visual execution results."""
from __future__ import annotations
import argparse
import itertools
import json
from pathlib import Path
from ch3.capability.registry import load_registry
from ch3.goal.goal_checker import goal_satisfied
from ch3.repair.persistent_model_recovery import generate_suffix
from ch3.schema.model_plan import GoalSpec, ModelPlan
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator
from ch3.vlm.client import DashScopeVLMClient
from scripts.symbolic_planner_baseline import search_suffix, BFS_VALID


def cases():
    result=[]
    objects=['blue_candidate','yellow_candidate','red_candidate']
    for level in ['simple','partial','constrained']:
        for layout, (a,b,c) in enumerate(list(itertools.permutations(objects))[:5]):
            active=[b] if level=='simple' else [a,b] if level=='partial' else [a,b,c]
            destinations={b:'green_region'}
            if level!='simple':destinations[a]='return_region'
            if level=='constrained':destinations[c]='third_region'
            scene=set(active)|set(destinations.values())
            at={obj:'table' for obj in scene}
            if level!='simple':at[a]='return_region'
            history=[]
            if level!='simple':history.extend([
                {'skill':'pick','object_id':a,'success':True},
                {'skill':'place','object_id':a,'target_id':'return_region','success':True}])
            history.append({'skill':'pick','object_id':b,'success':True})
            result.append({'case_id':f'{level}_{layout:02d}','level':level,'layout_seed':layout,
                'objects':sorted(scene),'at':at,'history':history,
                'current_state_source':'constructed_symbolic_fixture_not_visual',
                'perturbation':'held_object_dropped_to_table',
                'goal':[f'on({obj}, {target})' for obj,target in destinations.items()]+['hand_empty(right)'],
                'protected_objects':[a] if level=='constrained' else [],
                'targets':['table',*sorted(set(destinations.values()))],
                'instruction':'Complete every listed goal from the current state. Do not replay already satisfied goals. '
                    + (f'Do not move {a} at any point.' if level=='constrained' else ''),
                'old_remaining_plan':[{'skill':'place','object_id':b,'target_id':'green_region'}]
                  + ([{'skill':'pick','object_id':c},{'skill':'place','object_id':c,'target_id':'third_region'}] if level=='constrained' else [])})
    return result


def search(case,state,goal,validator):
    found,raw,reason=search_suffix(state=state,goal=goal,valid_targets=validator.valid_targets,
        allowed_skills={'pick','place'},mode=BFS_VALID,max_actions=8,arms={'right'})
    if not found:return None,reason
    plan=ModelPlan.model_validate({'actions':[dict(a,step_id=i+1) for i,a in enumerate(raw)]})
    checked=validator.validate(plan,state)
    ok=checked.valid and goal_satisfied(checked.final_state,goal,{'right'}) and not any(
        a.object_id in case['protected_objects'] for a in plan.actions)
    return (plan if ok else None),reason if ok else 'constraint_or_goal_rejected'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--env-file',type=Path)
    parser.add_argument('--freeze-only',action='store_true')
    args=parser.parse_args()
    if args.freeze_only:
        args.manifest.parent.mkdir(parents=True,exist_ok=True)
        with args.manifest.open('x') as f:
            for case in cases():f.write(json.dumps(case)+'\n')
        return
    if not args.output or not args.env_file:parser.error('output and env-file required')
    client=DashScopeVLMClient(env_path=args.env_file,timeout=60,max_retries=0)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as f:
        for line in args.manifest.read_text().splitlines():
            case=json.loads(line)
            state=WorldState(objects=set(case['objects']),at=case['at'])
            registry=load_registry(); registry._arms={'right'}
            validator=Validator(state.objects,registry)
            goal=GoalSpec(facts=case['goal'])
            for method in ['SYMBOLIC_SEARCH','MODEL_RESAMPLE','MODEL_FEEDBACK']:
                audit=[]
                if method=='SYMBOLIC_SEARCH':plan,reason=search(case,state,goal,validator)
                else:
                    plan,audit=generate_suffix(client=client,state=state,goal=goal,validator=validator,
                        history=case['history'],attempts=2,instruction=case['instruction'],targets=case['targets'],
                        protected_objects=case['protected_objects'],feedback_enabled=method=='MODEL_FEEDBACK')
                    reason='accepted' if plan else 'candidate_rejected'
                row={'case_id':case['case_id'],'level':case['level'],'method':method,
                    'symbolic_ready':plan is not None,'reason':reason,
                    'plan':plan.model_dump(mode='json') if plan else None,'calls':len(audit),
                    'audit':audit,'physical_success':None,'scope':'symbolic_difficulty_pilot'}
                f.write(json.dumps(row)+'\n');f.flush()
                print(json.dumps({k:row[k] for k in ['case_id','method','symbolic_ready','calls']}),flush=True)
    print(json.dumps({'completed_cases':45,'scope':'symbolic_only_not_physical'}),flush=True)

if __name__=='__main__':main()
