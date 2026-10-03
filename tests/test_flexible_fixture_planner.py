import json
from types import SimpleNamespace

from ch3.repair.flexible_fixture_planner import FlexibleFixturePlanner, evaluate_flexible, diagnostic_feedback
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


def test_second_feedback_is_diagnostic_and_full_obligations_remain_authoritative(tmp_path):
    image=tmp_path/'rgb.png';image.write_bytes(b'fixture')
    class Client:
        calls=0
        def complete(self,**kwargs):
            self.calls+=1
            request=json.loads(kwargs['user_prompt'])
            assert request['remaining_goal_facts']==['on(blue_candidate, green_region)',
                'on(yellow_candidate, return_region)']
            assert 'Rejected candidates were NEVER executed' in request['instruction']
            assert 'COMPLETE replacement suffix' in request['instruction']
            pairs=[('yellow_candidate','return_region')]
            if self.calls==2:
                assert request['feedback']['omitted_goal_count']==1
                assert 'unmet_goal_facts' not in request['feedback']
                assert 'rejected_candidate' not in request['feedback']
                assert [o['object_id'] for o in request['current_task_obligations']
                    if o['requires_delivery']]==['blue_candidate','yellow_candidate']
                pairs.insert(0,('blue_candidate','green_region'))
            return SimpleNamespace(content=plan(pairs).model_dump_json(),total_tokens=10)
    planner=FlexibleFixturePlanner(Client(),recovery=True,shared={},update_goals=True)
    planner.observe_release('blue_candidate','return_region')
    assert len(planner.remaining([],image,completed=1).steps)==4
    assert len(planner.audit[1]['local_feedback_evidence']['rejected_candidate']['actions'])==2


def test_feedback_projection_does_not_leak_rejected_plan_or_simulated_state():
    rejection=dict(error_code='GOAL_NOT_SATISFIED',unmet_goal_facts=['on(blue, green)'],
        predicted_final_facts=['on(yellow, return)'],rejected_output='untrusted model output',
        rejected_candidate={'actions':[]})
    result=diagnostic_feedback(rejection)
    assert result['candidate_executed'] is False
    assert result['omitted_goal_count']==1
    assert not {'unmet_goal_facts','predicted_final_facts','rejected_output','rejected_candidate'} & result.keys()
    assert rejection['predicted_final_facts']==['on(yellow, return)']


def test_rejected_simulation_never_advances_current_state(tmp_path):
    image=tmp_path/'rgb.png';image.write_bytes(b'fixture')
    class Client:
        def complete(self,**kwargs):
            return SimpleNamespace(content=plan([('yellow_candidate','return_region')]).model_dump_json(),total_tokens=1)
    planner=FlexibleFixturePlanner(Client(),recovery=True,shared={},update_goals=True)
    planner.observe_release('blue_candidate','return_region')
    facts=planner.state.facts().copy();history=list(planner.history)
    import pytest
    with pytest.raises(ValueError,match='flexible_remaining_repair_rejected'):
        planner.remaining([],image,completed=1)
    assert planner.state.facts()==facts and planner.history==history
    assert all(a['request']['remaining_goal_facts']==['on(blue_candidate, green_region)',
        'on(yellow_candidate, return_region)'] for a in planner.audit)


def test_off_goal_release_updates_observed_support_not_completed_history(tmp_path):
    image=tmp_path/'rgb.png';image.write_bytes(b'fixture')
    class Client:
        def complete(self,**kwargs):
            request=json.loads(kwargs['user_prompt'])
            assert 'on(blue_candidate, observed_support)' in request['current_facts']
            assert request['executed_history']==[]
            return SimpleNamespace(content=plan([('blue_candidate','return_region'),
                ('yellow_candidate','green_region')]).model_dump_json(),total_tokens=10)
    planner=FlexibleFixturePlanner(Client(),recovery=True,shared={})
    result=planner.released_object_repair(image,object_id='blue_candidate',
        release={'status':'release_supported'},observed_goal={'status':'not_satisfied'})
    assert len(result.steps)==4 and planner.history==[]
    assert planner.state.location_of('blue_candidate')=='observed_support'


def test_unknown_release_cannot_authorize_recovery_or_change_state(tmp_path):
    import pytest
    planner=FlexibleFixturePlanner(None,recovery=True,shared={})
    facts=planner.state.facts()
    with pytest.raises(ValueError,match='unsupported_released_object_recovery_evidence'):
        planner.released_object_repair(tmp_path/'absent',object_id='blue_candidate',
            release={'status':'unknown'},observed_goal={'status':'not_satisfied'})
    assert planner.state.facts()==facts and not planner.audit


def test_second_delivery_deviation_preserves_history_and_current_goal_protection(tmp_path):
    image=tmp_path/'rgb.png';image.write_bytes(b'fixture')
    for hard in (False,True):
        class Client:
            def complete(self,**kwargs):
                request=json.loads(kwargs['user_prompt'])
                assert request['executed_history']==[['blue_candidate','return_region']]
                if hard:
                    assert request['forbidden_objects']==[]
                    pairs=[('blue_candidate','green_region'),('yellow_candidate','return_region')]
                else:
                    assert request['forbidden_objects']==['blue_candidate']
                    pairs=[('yellow_candidate','green_region')]
                return SimpleNamespace(content=plan(pairs).model_dump_json(),total_tokens=10)
        planner=FlexibleFixturePlanner(Client(),recovery=True,shared={},update_on_deviation=hard)
        planner.observe_release('blue_candidate','return_region')
        result=planner.released_object_repair(image,object_id='yellow_candidate',
            release={'status':'release_supported'},observed_goal={'status':'not_satisfied'})
        assert len(result.steps)==(4 if hard else 2)
        assert len(planner.history)==1
        assert len(planner.goal_updates)==int(hard)


def test_v7_occupied_destination_rejected_but_clearing_order_accepted():
    state=WorldState.table_scene(OBJECTS)
    state.at['blue_candidate']='return_region'
    state.at['yellow_candidate']='observed_support'
    goals=dict(blue_candidate='green_region',yellow_candidate='return_region')
    bad=plan([('yellow_candidate','return_region'),('blue_candidate','green_region')])
    # Old result remains reproducible; new gate adds execution prerequisites.
    assert evaluate_flexible(bad,state,goals)[0] is not None
    error=evaluate_flexible(bad,state,goals,execution_contract=True,remaining_transfers=2)[1]
    assert error['error_code']=='DESTINATION_OCCUPIED'
    assert error['occupying_objects']==['blue_candidate']
    good=plan([('blue_candidate','green_region'),('yellow_candidate','return_region')])
    assert evaluate_flexible(good,state,goals,execution_contract=True,remaining_transfers=2)[0]
    assert evaluate_flexible(good,state,goals,execution_contract=True,
        remaining_transfers=1)[1]['error_code']=='REMAINING_EXECUTION_BUDGET'


def test_v7_execution_event_is_not_unexecuted_candidate():
    result=diagnostic_feedback(dict(error_code='OBSERVED_GOAL_NOT_SATISFIED',
        attempted_action={'skill':'place','object_id':'yellow_candidate','target_id':'green_region'},
        observation={'goal_status':'not_satisfied'}))
    assert result['event_type']=='execution_event' and result['action_executed']
    assert 'candidate_executed' not in result
    assert result['attempted_action']['target_id']=='green_region'


def test_v7_both_methods_share_occupancy_and_remaining_budget(tmp_path):
    image=tmp_path/'rgb.png';image.write_bytes(b'fixture')
    requests=[]
    for enabled in (False,True):
        class Client:
            def complete(self,**kwargs):
                requests.append(json.loads(kwargs['user_prompt']))
                return SimpleNamespace(content=plan([('blue_candidate','green_region'),
                    ('yellow_candidate','return_region')]).model_dump_json(),total_tokens=1)
        planner=FlexibleFixturePlanner(Client(),recovery=True,shared={},
            error_feedback=enabled,update_on_deviation=True,execution_contract=True)
        planner.observe_release('blue_candidate','return_region')
        planner.set_execution_progress(2)
        planner.released_object_repair(image,object_id='yellow_candidate',
            release={'status':'release_supported'},observed_goal={'status':'not_satisfied'})
        assert planner.state.location_of('yellow_candidate')=='observed_support'
        assert len(planner.history)==1
    direct,feedback=requests
    assert direct.pop('rejected_candidate_review') is None
    assert feedback.pop('rejected_candidate_review') is None
    assert direct==feedback
    assert direct['budget']['remaining_transfers']==2
    assert direct['objects'][0]['location']=='return_region'


def test_snapshot_separates_candidate_from_observation():
    p=FlexibleFixturePlanner(None,recovery=True,shared={},execution_contract=True)
    p.state.at['blue_candidate']='return_region'
    before=p.state.facts().copy()
    evidence=dict(error_code='DESTINATION_OCCUPIED',
        rejected_candidate=plan([('yellow_candidate','return_region')]).model_dump(mode='json'),
        predicted_final_facts=['untrusted'])
    snapshot=p.planning_snapshot(evidence)
    assert snapshot['rejected_candidate_review']['candidate_executed'] is False
    assert 'predicted_final_facts' not in snapshot['rejected_candidate_review']['violations']
    snapshot['rejected_candidate_review']['candidate']['actions'].clear()
    assert evidence['rejected_candidate']['actions']
    assert p.state.facts()==before


def test_v8_rejected_order_is_reviewed_without_executing_or_changing_state(tmp_path):
    image=tmp_path/'rgb.png';image.write_bytes(b'fixture')
    class Client:
        calls=0
        def complete(self,**kwargs):
            self.calls+=1
            request=json.loads(kwargs['user_prompt'])
            pairs=[('yellow_candidate','return_region'),('blue_candidate','green_region')]
            if self.calls==2:
                review=request['rejected_candidate_review']
                assert review['candidate_executed'] is False
                assert review['violations']['error_code']=='DESTINATION_OCCUPIED'
                assert request['objects'][0]['location']=='return_region'
                pairs.reverse()
            return SimpleNamespace(content=plan(pairs).model_dump_json(),total_tokens=1)
    p=FlexibleFixturePlanner(Client(),recovery=True,shared={},execution_contract=True)
    p.goals=dict(blue_candidate='green_region',yellow_candidate='return_region')
    p.state.at['blue_candidate']='return_region'
    p.state.at['yellow_candidate']='observed_support'
    p.set_execution_progress(2)
    before=p.state.facts().copy()
    assert p._remaining_request(image,None)
    assert p.state.facts()==before
    assert [a['accepted'] for a in p.audit]==[False,True]


def test_v7_progress_is_monotonic_and_bounded():
    import pytest
    p=FlexibleFixturePlanner(None,recovery=True,shared={},execution_contract=True)
    p.set_execution_progress(3)
    for invalid in (2,5,-1):
        with pytest.raises(ValueError,match='invalid_execution_progress'):
            p.set_execution_progress(invalid)


def test_occupied_rejection_feedback_is_concrete_not_a_rewritten_plan():
    result=diagnostic_feedback(dict(error_code='DESTINATION_OCCUPIED',object_id='yellow_candidate',
        target_id='return_region',occupying_objects=['blue_candidate'],
        rejected_candidate={'actions':[]},predicted_final_facts=['fake']))
    assert result['violated_precondition']==dict(object_id='yellow_candidate',
        target_id='return_region',occupying_objects=['blue_candidate'])
    assert 'rejected_candidate' not in result and 'predicted_final_facts' not in result
