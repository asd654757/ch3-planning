"""Real executed symbolic prefixes followed by fixed recovery disturbances."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from compare_supervision_decisions import SymbolicScene,Oracle
from ch3.supervision.core import Task,Supervisor
from ch3.supervision.final_variants import StateFirstDirectSupervisor,WithoutConflictSupervisor
from ch3.supervision.comparison import SharedInitialPlanner
from ch3.supervision.runtime import Session,JsonlJournal,run_session
from ch3.supervision.planner import ClientPlanner
from ch3.vlm.client import DashScopeVLMClient
from ch3.state.world_state import WorldState
from ch3.capability.registry import CapabilityRegistry
from ch3.validator.pipeline import Validator

METHODS={'STATE_FIRST_DIRECT':StateFirstDirectSupervisor,'WITHOUT_CONFLICT':WithoutConflictSupervisor,'SUPERVISED_RECOVERY':Supervisor}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--seeds',type=int,default=5)
    p.add_argument('--scripted',action='store_true');args=p.parse_args()
    if args.seeds<1:p.error('positive seed count required')
    client=None if args.scripted else DashScopeVLMClient(model='deepseek-v4.1-flash',env_path=Path.home()/'.config/ch3-planning/dashscope.env',timeout=60,max_retries=0)
    registry=CapabilityRegistry({'arms':['right'],'capabilities':{
        'pick':{'primitive':'grasp','args':['object_id','arm'],'policy':'symbolic_pick'},
        'place':{'primitive':'place','args':['object_id','target_id','arm'],'policy':'symbolic_place'}}})
    rows=[]
    with JsonlJournal(args.output) as journal:
        journal.emit({'event':'history_protocol','evidence_kind':'symbolic_only','scripted':args.scripted,
            'model':None if args.scripted else 'deepseek-v4.1-flash','seeds':args.seeds,
            'methods':list(METHODS),'prefix_source':'common deterministic six-action plan; three actions actually dispatched',
            'limits':{'model_calls_including_replayed_initial':6,'commands_including_prefix':12,'recovery_observations':15},
            'exploratory':True,'visual_feedback':False,'natural_failure':False})
        for profile in ['lost_held','wrong_held','goal_regression_and_loss']:
            for seed in range(args.seeds):
                objects=[f'part_{seed}_{i}' for i in range(4)];targets=[f'bin_{seed}_{i}' for i in range(3)]
                goals=tuple(f'on({objects[i]}, {targets[i]})' for i in range(3))
                actions=[]
                for obj,target in zip(objects,targets):
                    actions.extend([dict(step_id=len(actions)+1,skill='pick',object_id=obj,arm='right'),
                                    dict(step_id=len(actions)+2,skill='place',object_id=obj,target_id=target,arm='right')])
                prefix=json.dumps({'actions':actions})
                paired_boundary=None
                for method,cls in METHODS.items():
                    delegate=Oracle() if args.scripted else ClientPlanner(client,seed=seed)
                    planner=SharedInitialPlanner(delegate,prefix)
                    state=WorldState.table_scene(objects+targets)
                    scene=SymbolicScene(state,goals)
                    sup=cls(Task('Put each part in its assigned bin. Restore disrupted goals, preserve goals still satisfied, and first release unrelated held objects on table.',goals),
                        Validator(state.objects,registry,special_targets={'table'}),planner,max_model_calls=6,max_commands=12)
                    def emit(e):journal.emit({**e,'profile':profile,'seed':seed,'method':method})
                    print(f'[history] profile={profile} seed={seed} method={method} start',flush=True)
                    for _ in range(3):
                        obs=scene.read();sup.observe(obs);emit({'event':'prefix_observation','sequence':obs.sequence,'facts':sorted(obs.state.facts())})
                        if sup.prepare()!='ready':raise RuntimeError('common prefix failed validation')
                        sup.execute_next(scene);emit({'event':'prefix_execution',**sup.history[-1]})
                    history_before=sup.history
                    before=sorted(scene.state.facts())
                    scene.state.holding.clear();scene.state.at[objects[1]]='table'
                    if profile=='wrong_held':scene.state.holding['right']=objects[3]
                    if profile=='goal_regression_and_loss':scene.state.at[objects[0]]='table'
                    after=sorted(scene.state.facts())
                    if paired_boundary is None:paired_boundary=after
                    if after!=paired_boundary:raise RuntimeError('unpaired recovery state')
                    emit({'event':'controlled_disturbance','before':before,'after':after,'history_length':len(history_before)})
                    audit_start=len(sup.audit)
                    result=run_session(Session(sup,scene,scene,'symbolic_only'),max_observations=15,emit=emit)
                    if sup.history[:3]!=history_before:raise RuntimeError('prefix mutated')
                    recovery=sup.audit[audit_start:]
                    if any(e.get('rejection',{}).get('type')=='VLMError' for e in recovery):raise RuntimeError('service failure, not method performance')
                    row={**result,'profile':profile,'seed':seed,'method':method,
                        'prefix_immutable':True,'recovery_api_calls':planner.actual_calls if client else 0,
                        'first_recovery_conflicts':recovery[0]['request'].get('state_conflicts',[]) if recovery else [],
                        'rejections':[e.get('rejection') for e in recovery if not e['accepted']],
                        'model_usage':getattr(delegate,'usage',[])}
                    emit({**row,'event':'case_summary'});rows.append(row)
                    print(f'[history] complete={result["confirmed_complete"]}',flush=True)
        summary={'event':'history_summary','cases':len(rows),'by_method':{m:{'successes':sum(r['confirmed_complete'] for r in rows if r['method']==m),'calls':sum(r['model_calls'] for r in rows if r['method']==m)} for m in METHODS}}
        journal.emit(summary);print(json.dumps(summary),flush=True)
if __name__=='__main__':main()
