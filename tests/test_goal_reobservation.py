import pytest
from ch3.execution.goal_reobservation import resolve_goal_evidence


def test_unknown_can_acquire_new_evidence_without_mutating_original():
    original = {'status':'unknown'}
    events = []
    result = resolve_goal_evidence(original, budget=1,
        decide=lambda e,b:'observe', observe=lambda:{'status':'not_satisfied'}, emit=events.append)
    assert result['status'] == 'not_satisfied'
    assert original == {'status':'unknown'}
    assert len(events) == 2


def test_unknown_stays_unknown_after_single_unsuccessful_probe():
    count=[]
    def observe():
        count.append(1)
        return {'status':'unknown'}
    assert resolve_goal_evidence({'status':'unknown'}, budget=1,
        decide=lambda e,b:'observe', observe=observe, emit=lambda e:None)['status']=='unknown'
    assert len(count)==1


@pytest.mark.parametrize('status,budget', [('satisfied',1),('not_satisfied',1),('unknown',0)])
def test_no_unnecessary_calls(status,budget):
    def forbidden(*args):
        raise AssertionError('must not call')
    assert resolve_goal_evidence({'status':status},budget=budget,decide=forbidden,
        observe=forbidden,emit=forbidden)['status']==status


def test_stop_never_authorizes_completion():
    assert resolve_goal_evidence({'status':'unknown'},budget=1,decide=lambda e,b:'stop',
        observe=lambda: pytest.fail('unexpected probe'),emit=lambda e:None)['status']=='unknown'


def test_live_decision_schema_and_shared_call_budget(tmp_path):
    import json
    from types import SimpleNamespace
    from ch3.repair.flexible_fixture_planner import FlexibleFixturePlanner
    class Client:
        def complete(self, **kwargs):
            request=json.loads(kwargs['user_prompt'])
            assert request['evidence']['status']=='unknown'
            assert 'current_facts' not in request
            return SimpleNamespace(content='{"decision":"observe"}', total_tokens=10)
    planner=FlexibleFixturePlanner(Client(), recovery=True, shared={})
    image=tmp_path/'frame.png'
    image.write_bytes(b'fixture')
    for _ in range(2):
        assert planner.choose_goal_observation(image,evidence={'status':'unknown'},remaining_observations=1)=='observe'
    assert planner.choose_goal_observation(image,evidence={'status':'unknown'},remaining_observations=1)=='stop'
    assert len(planner.audit)==2
