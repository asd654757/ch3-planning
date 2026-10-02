"""Bounded model suffix generation; never treat executed history as current state."""
from __future__ import annotations
import json
import re
from ch3.goal.goal_checker import goal_satisfied
from ch3.schema.model_plan import ModelPlan
from pydantic import ValidationError


def strict_plan(text):
    text = text.strip()
    fence = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', text, re.S)
    if fence:
        text = fence.group(1)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate_key')
            result[key] = value
        return result
    data = json.loads(text, object_pairs_hook=unique)
    if not isinstance(data, dict) or set(data) != {'actions'}:
        raise ValueError('unexpected_envelope')
    if not isinstance(data['actions'], list) or not 1 <= len(data['actions']) <= 8:
        raise ValueError('action_budget')
    for action in data['actions']:
        if not isinstance(action, dict) or set(action) - {'step_id','skill','object_id','target_id','arm'}:
            raise ValueError('unexpected_action_fields')
    return ModelPlan.model_validate(data)


def generate_suffix(*, client, state, goal, validator, history, attempts=1, instruction=None, targets=None, protected_objects=(), feedback_enabled=True):
    """Both settings retain Validator AND Goal Checker before any execution.

    attempts=2 adds error-conditioned correction, not an unfair goal-check removal.
    A second call is made only on rejection. All attempted calls are accounted.
    """
    if attempts not in (1, 2):
        raise ValueError('attempts must be 1 or 2')
    audit = []
    feedback = None
    for index in range(attempts):
        request = {
            'instruction': instruction or 'Place red_cube_0 on tray_1 and finish with the right hand empty.',
            'current_facts': sorted(state.facts() | state.empty_hand_facts({'right'})),
            'goal': goal.facts, 'objects': sorted(state.objects),
            'skills': ['pick','place'], 'arms': ['right'],
            'targets': targets or ['table','tray_1'],
            'executed_history_NOT_current_state': history,
            'rejection_feedback': feedback if feedback_enabled else None,
            **({'forbidden_objects': list(protected_objects)} if protected_objects else {}),
            'output_schema': ({'actions': [{'step_id':'integer starting at 1', 'skill':'pick or place',
                'object_id':'one of objects', 'target_id':'required for place; omit for pick', 'arm':'right'}]}
                if instruction else {'actions': [{'step_id':1,'skill':'pick','object_id':'red_cube_0','arm':'right'}]}),
        }
        item = {'attempt': index + 1, 'request': request, 'accepted': False}
        audit.append(item)
        try:
            response = client.complete(system_prompt=(
                'Return only a JSON object with actions. Plan ONLY the remaining task from CURRENT facts. '
                'History records what happened, not what is still true. If a previously grasped object '
                'has dropped to the table, pick it again before placing. Use contiguous step IDs from 1. '
                'Every arm holds at most one object; end empty. Do not invent objects or targets.'),
                user_prompt=json.dumps(request), seed=0, temperature=.1, max_tokens=1024, json_mode=False)
            item.update(content=response.content, total_tokens=response.total_tokens,
                        latency_ms=response.latency_ms, finish_reason=response.finish_reason)
            plan = strict_plan(response.content)
            check = validator.validate(plan, state)
            item['valid'] = check.valid
            item['goal_satisfied'] = bool(check.valid and goal_satisfied(check.final_state, goal, {'right'}))
            item['constraints_satisfied'] = not any(a.object_id in protected_objects for a in plan.actions)
            if item['goal_satisfied'] and item['constraints_satisfied']:
                item['accepted'] = True
                return plan, audit
            feedback = {'error_code': str(check.error_code), 'message': check.message,
                        'goal_satisfied': item['goal_satisfied'], 'constraints_satisfied':item['constraints_satisfied'], 'rejected_output':response.content}
        except Exception as exc:
            # Never log transport exception strings (may contain service payloads).
            feedback = {'error_type': type(exc).__name__}
            if isinstance(exc, ValidationError):
                feedback['schema_errors'] = [{'location':list(e['loc']),'type':e['type'],'message':e['msg']}
                                            for e in exc.errors()]
            if 'content' in item:
                feedback['rejected_output'] = item['content']
        item['rejection'] = feedback
    return None, audit
