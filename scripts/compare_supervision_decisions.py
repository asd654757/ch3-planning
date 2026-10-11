"""Exploratory real-model multi-goal decisions; symbolic transitions, not physics."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ch3.supervision.core import Task,Observation,Evidence,Truth,Receipt,Supervisor
from ch3.supervision.final_variants import StateFirstDirectSupervisor,WithoutConflictSupervisor
from ch3.supervision.runtime import Session,JsonlJournal,run_session
from ch3.supervision.planner import ClientPlanner
from ch3.vlm.client import DashScopeVLMClient
from ch3.state.world_state import WorldState
from ch3.state.simulator import step
from ch3.schema.model_plan import ModelPlanAction
from ch3.capability.registry import CapabilityRegistry
from ch3.validator.pipeline import Validator

class SymbolicScene:
    def __init__(self,state,goals):
        self.state=state; self.goals=goals; self.sequence=0
    def read(self):
        facts=self.state.facts()
        result=Observation('symbolic-decision',self.sequence,self.state.copy(),
            {'right':self.state.holding.get('right')},
            {f:Evidence(Truth.TRUE if f in facts else Truth.FALSE,'symbolic_state','not physical evidence') for f in self.goals})
        self.sequence+=1
        return result
    def execute(self,executable,**kwargs):
        action=ModelPlanAction(step_id=1,skill=executable.source_skill,**executable.args)
        self.state,ok,_,_=step(self.state,action)
        return Receipt('success' if ok else 'failed','symbolic transition only')

class Oracle:
    def generate(self,r,images):
        actions=[]
        def add(skill,obj,target=None):
            a=dict(step_id=len(actions)+1,skill=skill,object_id=obj,arm='right')
            if target: a['target_id']=target
            actions.append(a)
        held=r['held_objects'].get('right')
        if held:add('place',held,'table')
        for fact in r['remaining_goal_facts']:
            obj,target=fact[3:-1].split(', ')
            add('pick',obj);add('place',obj,target)
        return json.dumps({'actions':actions})

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('--scripted',action='store_true');p.add_argument('--seeds',type=int,default=10)
    args=p.parse_args()
    client=None if args.scripted else DashScopeVLMClient(model='deepseek-v4.1-flash',env_path=Path.home()/'.config/ch3-planning/dashscope.env',timeout=60,max_retries=0)
    registry=CapabilityRegistry({'arms':['right'],'capabilities':{
        'pick':{'primitive':'grasp','args':['object_id','arm'],'policy':'symbolic_pick'},
        'place':{'primitive':'place','args':['object_id','target_id','arm'],'policy':'symbolic_place'}}})
    methods={'STATE_FIRST_DIRECT':StateFirstDirectSupervisor,'WITHOUT_CONFLICT':WithoutConflictSupervisor,'SUPERVISED_RECOVERY':Supervisor}
    rows=[]
    with JsonlJournal(args.output) as journal:
        journal.emit({'event':'protocol','evidence_kind':'symbolic_only','model':None if args.scripted else 'deepseek-v4.1-flash','exploratory':True,'profiles':['partial_goals','wrong_held','combined'],'seeds':args.seeds,'same_budgets':True,'natural_failure':False})
        for profile in ['partial_goals','wrong_held','combined']:
            for seed in range(args.seeds):
                cubes=[f'part_{seed}_{i}' for i in range(5)]; targets=[f'bin_{seed}_{i}' for i in range(3)]
                goals=tuple(f'on({cubes[i]}, {targets[i]})' for i in range(3))
                state=WorldState.table_scene(cubes+targets)
                protected=set()
                if profile!='wrong_held':
                    for i in range(2):state.at[cubes[i]]=targets[i];protected.add(cubes[i])
                if profile!='partial_goals':state.holding['right']=cubes[4]
                task=Task('Complete all three part-to-bin goals. Preserve already completed placements. If holding an unrelated part, first put it on the table. Do not move protected parts.',goals,frozenset(protected))
                for name,cls in methods.items():
                    planner=Oracle() if args.scripted else ClientPlanner(client,seed=seed)
                    sup=cls(task,Validator(state.objects,registry,special_targets={'table'}),planner,max_model_calls=6,max_commands=12)
                    scene=SymbolicScene(state.copy(),goals)
                    print(f'[decision] profile={profile} seed={seed} method={name} start',flush=True)
                    result=run_session(Session(sup,scene,scene,'symbolic_only'),max_observations=15,emit=lambda e:journal.emit({**e,'profile':profile,'seed':seed,'method':name}))
                    if any(e.get('rejection',{}).get('type')=='VLMError' for e in sup.audit):raise RuntimeError('Service failure; not a method result')
                    row={**result,'profile':profile,'seed':seed,'method':name,'protected_preserved':all(scene.state.at[o]==state.at[o] for o in protected),'rejections':[e.get('rejection') for e in sup.audit if not e['accepted']]}
                    rows.append(row);journal.emit({**row,'event':'case_summary'})
                    print(f'[decision] complete={result["confirmed_complete"]}',flush=True)
        summary={'event':'decision_summary','cases':len(rows),'by_method':{m:{'successes':sum(r['confirmed_complete'] for r in rows if r['method']==m),'calls':sum(r['model_calls'] for r in rows if r['method']==m)} for m in methods}}
        journal.emit(summary);print(json.dumps(summary),flush=True)
if __name__=='__main__':main()
