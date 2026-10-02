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
