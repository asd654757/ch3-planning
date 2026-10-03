import json
from types import SimpleNamespace

from ch3.repair.flexible_fixture_planner import FlexibleFixturePlanner, evaluate_flexible
from ch3.repair.persistent_model_recovery import strict_plan
from ch3.repair.sequence_model_planner import OBJECTS
from ch3.state.world_state import WorldState


def plan(pairs):
    actions=[]
    for obj,region in pairs:
        actions.extend([dict(step_id=len(actions)+1,skill='pick',object_id=obj),
                        dict(step_id=len(actions)+2,skill='place',object_id=obj,target_id=region)])
    return strict_plan(json.dumps(dict(actions=actions)),fixed_right_arm=True)


def test_different_transfer_orders_are_accepted_not_fixed_answer():
    goals=dict(blue_candidate='return_region',yellow_candidate='green_region')
    for pairs in [list(goals.items()),list(goals.items())[::-1]]:
        executable,error=evaluate_flexible(plan(pairs),WorldState.table_scene(OBJECTS),goals)
        assert executable and error is None


def test_regions_are_not_movable_and_completed_goals_protected():
    goals=dict(blue_candidate='return_region',yellow_candidate='green_region')
    state=WorldState.table_scene(OBJECTS);state.at['blue_candidate']='return_region'
    assert evaluate_flexible(plan(list(goals.items())),state,goals,{'blue_candidate'})[1]['error_code']=='PROTECTED_OBJECT_MUTATION'
    bad=plan([('green_region','return_region'),*goals.items()])
    assert evaluate_flexible(bad,WorldState.table_scene(OBJECTS),goals)[0] is None


def test_observed_history_and_changed_goal_drive_real_remaining_request(tmp_path):
    image=tmp_path/'rgb.png';image.write_bytes(b'fixture')
    class Client:
        calls=0
        def complete(self,**kwargs):
            self.calls+=1
            request=json.loads(kwargs['user_prompt'])
            if self.calls==1:
                candidate=plan(list(request['goals'].items()))
            else:
                assert 'on(blue_candidate, return_region)' in request['current_facts']
                assert request['goals']['blue_candidate']=='green_region'
                assert request['executed_history']==[['blue_candidate','return_region']]
                assert request['forbidden_objects']==[]
                candidate=plan([('yellow_candidate','return_region'),('blue_candidate','green_region')])
            return SimpleNamespace(content=candidate.model_dump_json(),total_tokens=10)
    client=Client();planner=FlexibleFixturePlanner(client,recovery=True,shared={},update_goals=True)
    initial=planner.initial(image)
    planner.observe_release('blue_candidate','return_region')
    remaining=planner.remaining(initial.steps[2:],image,completed=1)
    assert client.calls==2 and len(remaining.steps)==4
    assert remaining.steps[0].args['object_id']=='yellow_candidate'


def test_unchanged_goals_use_valid_suffix_without_model_call(tmp_path):
    image=tmp_path/'rgb.png';image.write_bytes(b'fixture')
    class Client:
        calls=0
        def complete(self,**kwargs):
            self.calls+=1
            return SimpleNamespace(content=plan([('yellow_candidate','green_region'),('blue_candidate','return_region')]).model_dump_json(),total_tokens=10)
    client=Client();planner=FlexibleFixturePlanner(client,recovery=True,shared={})
    initial=planner.initial(image)
    planner.observe_release('yellow_candidate','green_region')
    assert planner.protected()=={'yellow_candidate'}
    assert len(planner.remaining(initial.steps[2:],image,completed=1).steps)==2
    assert client.calls==1


def test_initial_shared_snapshot_is_immutable_after_goal_update(tmp_path):
    image=tmp_path/'rgb.png';image.write_bytes(b'fixture')
    class Client:
        calls=0
        def complete(self,**kwargs):
            self.calls+=1
            return SimpleNamespace(content=plan([('blue_candidate','return_region'),
                ('yellow_candidate','green_region')]).model_dump_json(),total_tokens=10)
    client=Client();shared={}
    first=FlexibleFixturePlanner(client,recovery=True,shared=shared,update_goals=True)
    assert first.initial(image)
    first.observe_release('blue_candidate','return_region')
    assert first.goal_updates
    assert first.audit[0]['request']['goal_updates']==[]
    assert shared['flexible_initial']['request']['goal_updates']==[]
    second=FlexibleFixturePlanner(client,recovery=False,shared=shared,update_goals=True)
    assert second.initial(image)
    assert client.calls==1
    assert second.audit[0]['shared_candidate']
