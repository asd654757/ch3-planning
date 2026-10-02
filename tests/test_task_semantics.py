import pytest
from pydantic import ValidationError
from ch3.vlm.task_semantics import TaskSemantics, deterministic_plan, evaluate_plan, semantic_equal
from scripts.task_semantics_pilot import cases

@pytest.mark.parametrize('row', cases(), ids=lambda r:r['case_id'])
def test_gold_is_reachable_or_refuses(row):
    c = TaskSemantics.model_validate(row['gold'])
    c.check_scope()
    plan = deterministic_plan(c)
    assert (plan is None) == (c.status != 'ready')
    if plan is not None: assert evaluate_plan(plan,c)['accepted']

def test_constraint_is_trajectory_not_final_location():
    c = TaskSemantics.model_validate(cases()[5]['gold'])
    plan = {'actions':[
        dict(step_id=1,skill='place',object_id='blue_candidate',target_id='table',arm='right'),
        dict(step_id=2,skill='pick',object_id='yellow_candidate',arm='right'),
        dict(step_id=3,skill='place',object_id='yellow_candidate',target_id='table',arm='right')]}
    audit = evaluate_plan(plan,c)
    assert audit['valid'] and audit['goal_satisfied']
    assert not audit['forbidden_ok'] and not audit['accepted']

def test_reverse_order_requires_temporary_release():
    c = TaskSemantics.model_validate(cases()[9]['gold'])
    plan = deterministic_plan(c)
    assert len(plan['actions']) == 5
    assert plan['actions'][0]['target_id'] == 'table'
    assert evaluate_plan(plan,c)['accepted']
    wrong = deterministic_plan(TaskSemantics.model_validate(cases()[8]['gold']))
    assert not evaluate_plan(wrong,c)['order_ok']

def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        TaskSemantics.model_validate({**cases()[0]['gold'], 'answer':'hidden'})

def test_unknown_object_and_partial_refusal_rejected():
    bad = {**cases()[0]['gold'], 'goals':[dict(object_id='orange', target_id='table')]}
    with pytest.raises(ValueError): TaskSemantics.model_validate(bad).check_scope()
    bad['status']='clarify'
    with pytest.raises(ValueError): TaskSemantics.model_validate(bad).check_scope()

def test_semantic_equal_detects_lost_prohibition():
    c = TaskSemantics.model_validate(cases()[5]['gold'])
    assert not semantic_equal(c,c.model_copy(update={'forbidden_objects':[]}))

from ch3.vlm.task_semantics import GroundedSemantics


def test_quotes_are_not_semantic_truth():
    # A real quote can still be attached to a false goal. No truth guarantee.
    c = cases()[0]['gold']
    raw = dict(contract=c, evidence=[dict(field='status',quote='Return blue'),
        dict(field='goals.0',quote='Return blue'),dict(field='hand_empty',quote='Return blue')])
    GroundedSemantics.model_validate(raw).check_evidence('Return blue to green.')

@pytest.mark.parametrize('evidence', [
    [dict(field='status',quote='invented quote')],
    [dict(field='status',quote='Return blue')],
    [dict(field='status',quote='Return blue'),dict(field='status',quote='Return blue')],
])
def test_evidence_rejects_missing_fabricated_duplicate(evidence):
    raw = dict(contract=cases()[0]['gold'], evidence=evidence)
    with pytest.raises(ValueError):
        GroundedSemantics.model_validate(raw).check_evidence('Return blue to table.')


def test_refusal_evidence_does_not_authorize_partial_request():
    c = cases()[-1]['gold']
    raw = dict(contract=c, evidence=[dict(field='status',quote='orange cube')])
    GroundedSemantics.model_validate(raw).check_evidence('Return blue then carry orange cube.')
    raw['contract']={**c,'goals':[dict(object_id='blue_candidate',target_id='table')]}
    with pytest.raises(ValueError):
        GroundedSemantics.model_validate(raw).check_evidence('Return blue then carry orange cube.')


def test_analysis_preserves_missing_denominator(tmp_path):
    import json
    from scripts.analyze_task_semantics import analyze
    (tmp_path/'manifest.json').write_text(json.dumps(dict(protocol='test',cases=cases())))
    row=dict(case_id='cancel_1',semantic_correct=False,end_to_end_symbolic_success=False,logged_calls=1,total_tokens=9,error='invalid contract',error_type='ValueError')
    (tmp_path/'records.jsonl').write_text(json.dumps(row)+'\n')
    a=analyze(tmp_path)
    assert a['expected']==12 and a['completed']==1 and len(a['missing_case_ids'])==11
    assert a['ready_expected']==10 and a['ready_gold_plan_success']==0
    (tmp_path/'records.jsonl').write_text((json.dumps(row)+'\n')*2)
    with pytest.raises(ValueError):analyze(tmp_path)

from ch3.vlm.task_semantics import SimpleGroundedSemantics

def test_v3_embedded_goal_quote():
    raw=dict(status='ready',reason_quote='',goals=[dict(object_id='blue_candidate',target_id='table',quote='Return blue to table')],forbidden_objects=[],placement_order=[],order_quote='',hand_empty=True,hand_quote='Return blue to table')
    c=SimpleGroundedSemantics.model_validate(raw).to_contract('Return blue to table.')
    assert c.goals[0].target_id=='table'
    raw['goals'][0]['quote']='not in instruction'
    with pytest.raises(ValueError):SimpleGroundedSemantics.model_validate(raw).to_contract('Return blue to table.')

def test_v3_partial_refusal_rejected():
    raw=dict(status='unsupported',reason_quote='orange',goals=[dict(object_id='blue_candidate',target_id='table',quote='blue')],forbidden_objects=[],placement_order=[],order_quote='',hand_empty=False,hand_quote='')
    with pytest.raises(ValueError):SimpleGroundedSemantics.model_validate(raw).to_contract('blue orange')


def test_v3_language_parser_has_no_image_or_gold(tmp_path):
    import json
    from types import SimpleNamespace
    from ch3.vlm.task_semantics import parse_semantics
    class Client:
        def complete(self,**kw):
            assert kw['image_path'] is None
            assert 'on(blue_candidate, green_region)' not in kw['user_prompt']
            assert 'previous_unexecuted_goal' not in kw['user_prompt']
            return SimpleNamespace(content=json.dumps(dict(status='clarify',reason_quote='there',goals=[],forbidden_objects=[],placement_order=[],order_quote='',hand_empty=False,hand_quote='')),model='test',total_tokens=1,latency_ms=1,finish_reason='stop',prompt_tokens=1,completion_tokens=1)
    c=parse_semantics(Client(),instruction='Put it there.',image_path=tmp_path/'nonexistent.png',log_path=tmp_path/'semantic_call.json')
    assert c.status=='clarify'
    assert json.loads((tmp_path/'semantic_call.json').read_text())['image_input'] is False
