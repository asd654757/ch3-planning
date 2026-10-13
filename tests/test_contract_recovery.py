import json
from ch3.repair.contract_recovery import snapshot,evaluate_output,request_for
from ch3.repair.sequence_model_planner import fixture_state


def encoded(pairs):
    actions=[]
    for o,t in pairs:
        actions += [dict(step_id=len(actions)+1,skill='pick',object_id=o,arm='right'),dict(step_id=len(actions)+2,skill='place',object_id=o,target_id=t,arm='right')]
    return json.dumps({'actions':actions})


def test_occupancy_goal_budget_and_valid_suffix():
    s=fixture_state();s.at['blue_candidate']='return_region'
    goals={'blue_candidate':'green_region','yellow_candidate':'return_region'}
    correct=encoded(list(goals.items()))
    assert evaluate_output(correct,s,goals,2)[1] is None
    err=evaluate_output(encoded(list(reversed(list(goals.items())))),s,goals,2)[1]
    assert err['error_code']=='DESTINATION_OCCUPIED' and err['first_invalid_step']==2
    assert evaluate_output(encoded([('blue_candidate','green_region')]),s,goals,2)[1]['error_code']=='GOAL_NOT_SATISFIED'
    assert evaluate_output(correct,s,goals,1)[1]['error_code']=='REMAINING_EXECUTION_BUDGET'
    assert evaluate_output('bad',s,goals,2)[1]['error_code']=='PARSE_FAILURE'


def test_methods_share_rules_and_evidence_no_action_answer():
    s=fixture_state();goals={'blue_candidate':'green_region','yellow_candidate':'return_region'}
    base=snapshot(s,goals,2)
    for m in ['DIRECT','DIAGNOSTIC','FULL']:
        req=request_for(base,m,'bad',{'error_code':'PARSE_FAILURE'})
        assert req['constraints']==base['constraints']
        assert req['current_observation']==base['current_observation']
        assert 'actions' not in req
    assert 'violation' not in request_for(base,'DIRECT','bad',{})['feedback']
