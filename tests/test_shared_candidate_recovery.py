import json
from types import SimpleNamespace
from scripts.shared_candidate_recovery_pilot import run_case
from scripts.recovery_difficulty_pilot import cases


def reply(content):
    return SimpleNamespace(content=content,total_tokens=5,latency_ms=0,finish_reason='stop')


def test_shared_failed_candidate_only_feedback_differs():
    case=cases()[0]
    obj=next(o for o in case['objects'] if o.endswith('candidate'))
    bad=json.dumps({'actions':[{'step_id':1,'skill':'place','object_id':obj,'target_id':'green_region'}]})
    good=json.dumps({'actions':[{'step_id':1,'skill':'pick','object_id':obj},
                               {'step_id':2,'skill':'place','object_id':obj,'target_id':'green_region'}]})
    class Client:
        def __init__(self):self.requests=[]
        def complete(self,**kwargs):
            self.requests.append(json.loads(kwargs['user_prompt']))
            return reply(bad if len(self.requests)<3 else good)
    client=Client();row=run_case(case,client)
    assert row['actual_model_calls']==3 and not row['first_accepted']
    assert not row['branches']['NO_ERROR_FEEDBACK']['symbolic_ready']
    assert row['branches']['ERROR_FEEDBACK']['symbolic_ready']
    a,b=client.requests[1:]
    assert a['previous_candidate']==b['previous_candidate']==bad
    assert a['rejection_feedback'] is None and b['rejection_feedback']['error_code'].endswith('OBJECT_NOT_HELD')
    assert {k:v for k,v in a.items() if k!='rejection_feedback'}=={k:v for k,v in b.items() if k!='rejection_feedback'}


def test_common_success_no_extra_calls():
    case=cases()[0];obj=next(o for o in case['objects'] if o.endswith('candidate'))
    good=json.dumps({'actions':[{'step_id':1,'skill':'pick','object_id':obj},
                               {'step_id':2,'skill':'place','object_id':obj,'target_id':'green_region'}]})
    class Client:
        def complete(self,**kwargs):return reply(good)
    row=run_case(case,Client(),index=1)
    assert row['first_accepted'] and row['actual_model_calls']==1
    assert row['branch_order'][0]=='ERROR_FEEDBACK'
    assert all(b['additional_calls']==0 and b['symbolic_ready'] for b in row['branches'].values())


def test_missing_response_kept_not_retried():
    class Client:
        def complete(self,**kwargs):raise TimeoutError()
    row=run_case(cases()[0],Client())
    assert row['first_candidate_sha256'] is None and row['actual_model_calls']==1
    assert all(b['status']=='common_call_missing_response' for b in row['branches'].values())
