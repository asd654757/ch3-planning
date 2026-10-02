import pytest
from types import SimpleNamespace
from ch3.repair.persistent_model_recovery import strict_plan, generate_suffix
from ch3.capability.registry import load_registry
from ch3.validator.pipeline import Validator
from ch3.schema.model_plan import GoalSpec
from ch3.state.world_state import WorldState

PICK='{"step_id":1,"skill":"pick","object_id":"red_cube_0","arm":"right"}'
PLACE='{"step_id":2,"skill":"place","object_id":"red_cube_0","target_id":"tray_1","arm":"right"}'
GOOD='{"actions":['+PICK+','+PLACE+']}'

@pytest.mark.parametrize('text', ['prefix '+GOOD, '{"actions":[],"actions":[]}', '{"actions":[],"extra":1}', '{"actions":[]}'])
def test_strict_rejection(text):
    with pytest.raises(ValueError): strict_plan(text)


def test_correction_and_current_state():
    registry=load_registry(); registry._arms={'right'}
    state=WorldState.table_scene({'red_cube_0','tray_1'})
    validator=Validator(state.objects, registry)
    replies=iter(['{"actions":['+PLACE.replace('"step_id":2','"step_id":1')+']}',GOOD])
    class Client:
        def complete(self, **kwargs):
            assert 'hand_empty(right)' in kwargs['user_prompt']
            return SimpleNamespace(content=next(replies), total_tokens=10, latency_ms=1, finish_reason='stop')
    plan, audit=generate_suffix(client=Client(), state=state, goal=GoalSpec(facts=['on(red_cube_0, tray_1)']),validator=validator, history=[{'success':True,'step_id':1}], attempts=2)
    assert plan is not None and len(audit)==2
    assert not audit[0]['accepted'] and audit[1]['accepted']
    assert not state.holding


def test_real_client_interface():
    from ch3.vlm.client import DashScopeVLMClient
    class Transport:
        def chat(self,payload):
            return {'choices':[{'message':{'content':GOOD},'finish_reason':'stop'}],
                    'usage':{'total_tokens':12}}
    registry=load_registry(); registry._arms={'right'}
    state=WorldState.table_scene({'red_cube_0','tray_1'})
    plan,audit=generate_suffix(client=DashScopeVLMClient(transport=Transport()),state=state,
        goal=GoalSpec(facts=['on(red_cube_0, tray_1)']),validator=Validator(state.objects,registry),history=[],attempts=1)
    assert plan is not None and audit[0]['total_tokens']==12


def test_protected_object_and_equal_budget():
    registry=load_registry(); registry._arms={'right'}
    state=WorldState.table_scene({'red_cube_0','tray_1'})
    class Client:
        def __init__(self):self.requests=[]
        def complete(self,**kwargs):
            import json
            self.requests.append(json.loads(kwargs['user_prompt']))
            return SimpleNamespace(content=GOOD,total_tokens=1,latency_ms=0,finish_reason='stop')
    for feedback in [False,True]:
        client=Client()
        plan,audit=generate_suffix(client=client,state=state,goal=GoalSpec(facts=['on(red_cube_0, tray_1)']),
            validator=Validator(state.objects,registry),history=[],attempts=2,
            protected_objects=['red_cube_0'],feedback_enabled=feedback)
        assert plan is None and len(audit)==2
        assert not audit[0]['constraints_satisfied']
        assert bool(client.requests[1]['rejection_feedback'])==feedback
