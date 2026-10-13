"""Natural proposals and shared controlled rejection, reported separately."""
import sys,json,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ch3.repair.contract_recovery import snapshot,evaluate_output,request_for
from ch3.repair.sequence_model_planner import fixture_state
from ch3.vlm.client import DashScopeVLMClient
from ch3.supervision.runtime import JsonlJournal

def pair(obj,target):
    return [{'step_id':1,'skill':'pick','object_id':obj,'arm':'right'},
            {'step_id':2,'skill':'place','object_id':obj,'target_id':target,'arm':'right'}]

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--seeds',type=int,default=5);a=p.parse_args()
    client=DashScopeVLMClient(model='deepseek-v4.1-flash',env_path=Path.home()/'.config/ch3-planning/dashscope.env',timeout=60,max_retries=0)
    rows=[]
    with JsonlJournal(a.output) as journal:
        journal.emit({'event':'protocol','version':'contract_recovery_v1','model':client.model,'evidence_kind':'symbolic_only',
            'seeds':a.seeds,'max_calls_natural':2,'max_calls_shared_rejection':1,'max_tokens':2048,
            'shared_safety_gate':True,'search_planner':False,'exploratory':True})
        for case in ['occupied_order','goal_coverage','budget_replay']:
            state=fixture_state();goals={'blue_candidate':'green_region','yellow_candidate':'return_region'}
            budget=2
            if case=='occupied_order':state.at['blue_candidate']='return_region'
            if case=='budget_replay':state.at['blue_candidate']='green_region';budget=1
            base=snapshot(state,goals,budget)
            bad=pair('yellow_candidate','return_region')
            if case=='occupied_order':bad+=pair('blue_candidate','green_region')
            if case=='budget_replay':bad=pair('blue_candidate','green_region')+pair('yellow_candidate','return_region')
            for i,x in enumerate(bad):x['step_id']=i+1
            bad=json.dumps({'actions':bad});_,bad_error=evaluate_output(bad,state,goals,budget)
            if bad_error is None:raise RuntimeError('controlled candidate must be rejected')
            for seed in range(a.seeds):
                for track in ['natural','shared_rejection']:
                    for method in ['DIRECT','DIAGNOSTIC','FULL']:
                        rejected,error=(bad,bad_error) if track=='shared_rejection' else (None,None)
                        accepted=False;calls=0;tokens=0
                        for attempt in range(1 if track=='shared_rejection' else 2):
                            req=request_for(base,method,rejected,error)
                            response=client.complete(system_prompt='Return only {"actions": [...]} JSON. Generate the COMPLETE remaining plan, not a patch. Use contiguous integer step_id from 1, skill pick/place, object_id, arm right, and target_id for place. At most eight actions. All task rules apply to every candidate step. Never treat a rejected candidate as observed state.',user_prompt=json.dumps(req),seed=seed,temperature=0.1,max_tokens=2048,json_mode=True)
                            calls+=1;tokens+=response.total_tokens
                            _,err=evaluate_output(response.content,state,goals,budget)
                            journal.emit({'event':'proposal','case':case,'seed':seed,'track':track,'method':method,'request':req,'output':response.content,'error':err,'finish_reason':response.finish_reason,'total_tokens':response.total_tokens,'latency_ms':response.latency_ms})
                            if err is None:accepted=True;break
                            rejected,error=response.content,err
                        row={'event':'case_summary','case':case,'seed':seed,'track':track,'method':method,'accepted':accepted,'calls':calls,'tokens':tokens,'last_error':err}
                        rows.append(row);journal.emit(row);print(json.dumps(row),flush=True)
        summary={'event':'contract_summary','cases':len(rows),'by_track':{t:{m:{'accepted':sum(r['accepted'] for r in rows if r['track']==t and r['method']==m),'calls':sum(r['calls'] for r in rows if r['track']==t and r['method']==m)} for m in ['DIRECT','DIAGNOSTIC','FULL']} for t in ['natural','shared_rejection']}}
        journal.emit(summary);print(json.dumps(summary),flush=True)
if __name__=='__main__':main()
