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
