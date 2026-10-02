import json
from pathlib import Path
import pytest
from ch3.vlm.task_semantics import TaskSemantics,deterministic_plan,evaluate_plan,generate_semantic_remaining_plan
from scripts.task_semantic_transfer_benchmark import score

DATA=Path(__file__).resolve().parents[1]/'data/scenarios/task_semantic_transfer_v1.jsonl'
ROWS=[json.loads(s) for s in DATA.read_text().splitlines()]

@pytest.mark.parametrize('row',ROWS,ids=lambda r:r['case_id'])
def test_oracle_reachable_not_model_gold_leak(row):
    c=TaskSemantics.model_validate(row['gold']);c.check_scope()
    raw=deterministic_plan(c)
    assert (raw is None)==(c.status!='ready')
    if raw is not None:assert evaluate_plan(raw,c)['accepted']
    assert score(c,raw,c)['strict_success']


def test_new_instructions_not_exact_development_duplicates():
    from scripts.task_semantics_pilot import cases
    assert not set(r['instruction'] for r in ROWS)&set(r['instruction'] for r in cases())
    assert len({r['instruction'] for r in ROWS})==24


def test_plan_call_plain_mode_strict_action_format(tmp_path):
    from types import SimpleNamespace
    class Client:
        def complete(self,**kw):
            assert kw['image_path'] is None and kw['json_mode'] is False
            assert 'MUST NOT include target_id' in kw['user_prompt']
            assert 'temporarily place blue on table, then pick/place yellow' not in kw['user_prompt']
            return SimpleNamespace(content='```json\n{"actions":[{"step_id":1,"skill":"place","object_id":"blue_candidate","target_id":"table","arm":"right"}]}\n```',model='test',total_tokens=1,latency_ms=1,finish_reason='stop',prompt_tokens=1,completion_tokens=1)
    raw=generate_semantic_remaining_plan(Client(),contract=TaskSemantics.model_validate(ROWS[0]['gold']),image_path=None,log_path=tmp_path/'plan_call.json')
    assert raw['actions'][0]['target_id']=='table'


def test_rule_refusal_on_ready_gold_is_not_success():
    from ch3.vlm.rule_task_semantics import refusal
    gold=TaskSemantics.model_validate(ROWS[0]['gold'])
    r=score(refusal('clarify'),None,gold)
    assert not r['strict_success'] and not r['correct_refusal'] and not r['misaccept']


def test_wrong_semantics_can_pass_online_but_not_gold():
    a=TaskSemantics.model_validate(ROWS[0]['gold'])
    b=TaskSemantics.model_validate(ROWS[10]['gold'])
    r=score(b,deterministic_plan(b),a)
    assert r['misaccept'] and not r['strict_success']
