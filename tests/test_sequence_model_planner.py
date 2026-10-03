import json
from types import SimpleNamespace
from ch3.repair.sequence_model_planner import SequenceModelPlanner


GOOD = {'actions': [dict(step_id=1, skill='pick', object_id='blue_candidate'),
    dict(step_id=2, skill='place', object_id='blue_candidate', target_id='return_region'),
    dict(step_id=3, skill='pick', object_id='yellow_candidate'),
    dict(step_id=4, skill='place', object_id='yellow_candidate', target_id='green_region')]}


def test_live_initial_shared_and_executed_suffix(tmp_path):
    image = tmp_path / 'frame.png'; image.write_bytes(b'fixture')
    class Client:
        calls = 0
        def complete(self, **kwargs):
            self.calls += 1
            return SimpleNamespace(content=json.dumps(GOOD), total_tokens=10)
    client = Client(); shared = {}
    a = SequenceModelPlanner(client, recovery=False, shared=shared)
    plan = a.initial(image)
    b = SequenceModelPlanner(client, recovery=True, shared=shared)
    assert b.initial(image).to_list() == plan.to_list()
    assert len(b.remaining(plan.steps[2:], image).steps) == 2
    assert client.calls == 1 and b.audit[0]['actual_model_calls'] == 0


def test_model_rejection_really_calls_model_for_repair(tmp_path):
    image = tmp_path / 'frame.png'; image.write_bytes(b'fixture')
    class Client:
        calls = 0
        def complete(self, **kwargs):
            self.calls += 1
            return SimpleNamespace(content=json.dumps({'actions': GOOD['actions'][1:]} if self.calls==1 else GOOD), total_tokens=10)
    client = Client()
    p = SequenceModelPlanner(client, recovery=True, shared={})
    assert len(p.initial(image).steps) == 4
    assert client.calls == 2 and p.audit[1]['request']['rejection_feedback'] is not None


def test_invalid_remaining_repaired_without_completed_pick(tmp_path):
    image = tmp_path / 'frame.png'; image.write_bytes(b'fixture')
    class Client:
        def complete(self, **kwargs):
            request = json.loads(kwargs['user_prompt'])
            assert request['forbidden_objects'] == ['blue_candidate']
            assert request['executed_history']
            actions = [dict(a, step_id=i+1) for i,a in enumerate(GOOD['actions'][2:])]
            return SimpleNamespace(content=json.dumps({'actions': actions}), total_tokens=10)
    from ch3.compiler.executable_plan import ExecutableStep
    bad = [ExecutableStep(1, 'fixed_place', 'place', dict(object_id='yellow_candidate', target_id='green_region', arm='right'), 'place')]
    p = SequenceModelPlanner(Client(), recovery=True, shared={})
    assert len(p.remaining(bad, image).steps) == 2
    assert all(a.object_id != 'blue_candidate' for a in __import__('ch3.schema.model_plan', fromlist=['ModelPlan']).ModelPlan.model_validate(p.audit[0]['normalized_plan']).actions)


def test_long_task_cannot_skip_intermediate_delivery():
    from ch3.repair.sequence_model_planner import evaluate, fixture_state
    from ch3.repair.persistent_model_recovery import strict_plan
    plan = strict_plan(json.dumps(GOOD), fixed_right_arm=True)
    executable, rejection = evaluate(plan, fixture_state(), long_task=True)
    assert executable is None and rejection['error_code'] == 'BACKEND_SEQUENCE_OUT_OF_SCOPE'


def test_long_task_repick_is_remaining_not_prefix_replay():
    from ch3.repair.sequence_model_planner import evaluate, fixture_state
    from ch3.repair.persistent_model_recovery import strict_plan
    state = fixture_state(completed=1, long_task=True)
    assert state.at['blue_candidate'] == 'green_region'
    plan = strict_plan(json.dumps(GOOD), fixed_right_arm=True)
    executable, rejection = evaluate(plan, state, completed=1, long_task=True)
    assert rejection is None and len(executable.steps) == 4
    state = fixture_state(completed=2, long_task=True)
    assert state.at['blue_candidate'] == 'return_region'


def test_flat_surface_pick_opt_in_preserves_legacy_validator():
    from ch3.repair.sequence_model_planner import fixture_state
    from ch3.repair.persistent_model_recovery import strict_plan
    from ch3.validator.pipeline import Validator
    from ch3.execution.observed_continuation import fixed_registry
    state = fixture_state(completed=1, long_task=True)
    plan = strict_plan(json.dumps(GOOD), fixed_right_arm=True)
    legacy = Validator(state.objects, fixed_registry()).validate(plan, state)
    supported = Validator(state.objects, fixed_registry(), pick_surfaces={'green_region'}).validate(plan, state)
    assert not legacy.valid and supported.valid


def test_execution_timeout_holding_repairs_place_without_repick(tmp_path):
    image = tmp_path/'frame.png'; image.write_bytes(b'fixture')
    class Client:
        calls = 0
        def complete(self, **kwargs):
            self.calls += 1
            request = json.loads(kwargs['user_prompt'])
            assert 'holding(right, blue_candidate)' in request['current_facts']
            actions = [dict(a, step_id=i+1) for i,a in enumerate(GOOD['actions'][1:])]
            return SimpleNamespace(content=json.dumps({'actions':actions}), total_tokens=10)
    from ch3.compiler.executable_plan import compile_plan
    from ch3.execution.observed_continuation import fixed_registry
    from ch3.repair.persistent_model_recovery import strict_plan
    steps = compile_plan(strict_plan(json.dumps(GOOD), fixed_right_arm=True), fixed_registry()).steps[1:]
    client = Client(); p = SequenceModelPlanner(client, recovery=True, shared={}, long_task=True)
    result = p.execution_repair(steps, image, completed=1, holding_object='blue_candidate',
                               event=dict(state_source='supported_RGB', error_code='PICK_TIMEOUT'))
    assert client.calls == 1 and result.steps[0].source_skill == 'place'
    assert p.audit[-1]['phase'] == 'execution_repair' and p.audit[-1]['accepted']


def test_off_target_release_state_is_not_fabricated_goal_or_table(tmp_path):
    image=tmp_path/'frame.png'; image.write_bytes(b'fixture')
    class Client:
        def complete(self, **kwargs):
            request=json.loads(kwargs['user_prompt'])
            assert 'on(yellow_candidate, observed_support)' in request['current_facts']
            assert 'on(yellow_candidate, green_region)' not in request['current_facts']
            assert 'on(yellow_candidate, table)' not in request['current_facts']
            return SimpleNamespace(content=json.dumps({'actions':[
                dict(step_id=1,skill='pick',object_id='yellow_candidate'),
                dict(step_id=2,skill='place',object_id='yellow_candidate',target_id='green_region')]}), total_tokens=10)
    from ch3.compiler.executable_plan import ExecutableStep
    steps=[ExecutableStep(1,'fixed_pick','grasp',dict(object_id='yellow_candidate',arm='right'),'pick'),
           ExecutableStep(2,'fixed_place','place',dict(object_id='yellow_candidate',arm='right',target_id='green_region'),'place')]
    p=SequenceModelPlanner(Client(), recovery=True, shared={}, long_task=True)
    result=p.execution_repair(steps,image,completed=2,observed_object='yellow_candidate',event=dict(state_source='RGB'))
    assert len(result.steps)==2 and p.audit[-1]['accepted']
