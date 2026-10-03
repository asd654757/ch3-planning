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


def test_feedback_comparison_has_same_inputs_gate_and_retry_budget(tmp_path):
    image=tmp_path/'rgb.png';image.write_bytes(b'fixture')
    requests=[]
    for feedback_enabled in (False,True):
        class Client:
            calls=0
            def complete(self,**kwargs):
                self.calls+=1
                requests.append((feedback_enabled,json.loads(kwargs['user_prompt'])))
                pairs=[('blue_candidate','green_region'),('yellow_candidate','return_region')]
                # Legal syntax but an incomplete goal, so both must reject it.
                if self.calls==1:
                    pairs=pairs[:1]
                return SimpleNamespace(content=plan(pairs).model_dump_json(),total_tokens=10)
        client=Client()
        planner=FlexibleFixturePlanner(client,recovery=True,shared={},update_goals=True,
            error_feedback=feedback_enabled)
        planner.observe_release('blue_candidate','return_region')
        executable=planner.remaining([],image,completed=1)
        assert len(executable.steps)==4 and client.calls==2
        assert [a['accepted'] for a in planner.audit]==[False,True]
    for i in (0,1):
        direct=requests[i][1].copy();feedback=requests[i+2][1].copy()
        assert direct.pop('feedback') is None
        assert feedback.pop('feedback') is not None
        assert direct==feedback


def test_rejected_outputs_stop_after_equal_budget(tmp_path):
    image=tmp_path/'rgb.png';image.write_bytes(b'fixture')
    import pytest
    for enabled in (False,True):
        class Client:
            calls=0
            def complete(self,**kwargs):
                self.calls+=1
                return SimpleNamespace(content='[]',total_tokens=1)
        client=Client()
        planner=FlexibleFixturePlanner(client,recovery=True,shared={},error_feedback=enabled)
        with pytest.raises(ValueError,match='flexible_remaining_repair_rejected'):
            planner.remaining([],image,completed=0)
        assert client.calls==2
        assert all(not a['accepted'] for a in planner.audit)


def test_missing_goal_feedback_names_actual_omission_without_truth():
    state=WorldState.table_scene(OBJECTS)
    state.at['blue_candidate']='return_region'
    goals=dict(blue_candidate='green_region',yellow_candidate='return_region')
    _,error=evaluate_flexible(plan([('yellow_candidate','return_region')]),state,goals)
    assert error['unmet_goal_facts']==['on(blue_candidate, green_region)']
    assert error['evidence_source']=='candidate_symbolic_simulation_not_simulator_truth'
    assert 'on(blue_candidate, return_region)' in error['predicted_final_facts']


def test_second_feedback_contains_rejected_candidate_and_specific_goal(tmp_path):
    image=tmp_path/'rgb.png';image.write_bytes(b'fixture')
    class Client:
        calls=0
        def complete(self,**kwargs):
            self.calls+=1
            request=json.loads(kwargs['user_prompt'])
            assert request['remaining_goal_facts']==['on(blue_candidate, green_region)',
                'on(yellow_candidate, return_region)']
            pairs=[('yellow_candidate','return_region')]
            if self.calls==2:
                assert request['feedback']['unmet_goal_facts']==['on(blue_candidate, green_region)']
                assert len(request['feedback']['rejected_candidate']['actions'])==2
                pairs.insert(0,('blue_candidate','green_region'))
            return SimpleNamespace(content=plan(pairs).model_dump_json(),total_tokens=10)
    planner=FlexibleFixturePlanner(Client(),recovery=True,shared={},update_goals=True)
    planner.observe_release('blue_candidate','return_region')
    assert len(planner.remaining([],image,completed=1).steps)==4
