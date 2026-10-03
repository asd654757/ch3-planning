"""Live model planning gate for a narrow persistent two-transfer backend."""
import hashlib
import json

from ch3.execution.observed_continuation import fixed_registry
from ch3.goal.goal_checker import goal_satisfied
from ch3.repair.persistent_model_recovery import strict_plan
from ch3.schema.model_plan import GoalSpec, ModelPlan
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator
from ch3.compiler.executable_plan import compile_plan

OBJECTS = {'blue_candidate', 'yellow_candidate', 'return_region', 'green_region'}
INSTRUCTION = ('First move the blue cube to the magenta return region. Then move the yellow cube '
               'to the green region. Leave the completed blue cube untouched and finish empty-handed.')


def fixture_state(completed=False):
    state = WorldState.table_scene(OBJECTS)
    if completed:
        state.at['blue_candidate'] = 'return_region'
    return state


def evaluate(plan, state, completed=False):
    registry = fixed_registry()
    result = Validator(OBJECTS, registry).validate(plan, state)
    goal = GoalSpec(facts=['on(blue_candidate, return_region)', 'on(yellow_candidate, green_region)', 'hand_empty(right)'])
    if not result.valid:
        return None, dict(error_code=str(result.error_code), message=result.message)
    if not goal_satisfied(result.final_state, goal, {'right'}):
        return None, dict(error_code='GOAL_NOT_SATISFIED', remaining_goal_facts=sorted(
            set(goal.facts) - (result.final_state.facts() | result.final_state.empty_hand_facts({'right'}))))
    # Explicit executor scope, never silently replace unsupported model plans.
    expected = [('pick', 'yellow_candidate', None), ('place', 'yellow_candidate', 'green_region')]
    if not completed:
        expected = [('pick', 'blue_candidate', None), ('place', 'blue_candidate', 'return_region')] + expected
    actual = [(a.skill.value, a.object_id, a.target_id) for a in plan.actions]
    if actual != expected:
        return None, dict(error_code='BACKEND_SEQUENCE_OUT_OF_SCOPE', supported_order=expected)
    return compile_plan(plan, registry), None


class SequenceModelPlanner:
    def __init__(self, client, *, recovery, shared):
        self.client, self.recovery, self.shared = client, recovery, shared
        self.audit = []

    def _request(self, state, image_path, completed, feedback=None, previous=None):
        prompt = dict(instruction=INSTRUCTION, current_facts=sorted(state.facts() | state.empty_hand_facts({'right'})),
            state_source='controlled_initial_fixture' if not completed else 'RGB_supported_blue_release_plus_untouched_yellow_fixture',
            objects=sorted(OBJECTS), targets=['return_region', 'green_region'],
            remaining_goal_facts=['on(yellow_candidate, green_region)', 'hand_empty(right)'] if completed else
                ['on(blue_candidate, return_region)', 'on(yellow_candidate, green_region)', 'hand_empty(right)'],
            executed_history=[] if not completed else ['pick blue_candidate', 'place blue_candidate return_region'],
            forbidden_objects=['blue_candidate'] if completed else [],
            backend_configuration={'arm': 'right', 'skills': ['pick', 'place']},
            output_schema={'actions': [{'step_id': 'contiguous from 1', 'skill': 'pick or place',
                'object_id': 'registered ID', 'target_id': 'required for place, omitted for pick'}]},
            rejection_feedback=feedback, previous_candidate=previous)
        item = dict(request=prompt, image_sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(),
                    actual_model_calls=0, accepted=False)
        self.audit.append(item)
        try:
            if not completed and feedback is None and 'initial' in self.shared:
                saved = self.shared['initial']
                if saved['request'] != prompt or saved['image_sha256'] != item['image_sha256']:
                    raise ValueError('shared_initial_input_mismatch')
                item.update(content=saved['content'], total_tokens=0, shared_candidate=True)
            else:
                item['actual_model_calls'] = 1
                response = self.client.complete(system_prompt='Return only JSON with actions. Generate the remaining pick/place plan from CURRENT state. Never replay completed actions. End with the right hand empty.',
                    user_prompt=json.dumps(prompt), image_path=image_path, temperature=.1, seed=0,
                    max_tokens=1024, json_mode=False)
                item.update(content=response.content, total_tokens=response.total_tokens)
                if not completed and feedback is None:
                    self.shared['initial'] = dict(request=prompt, image_sha256=item['image_sha256'], content=response.content)
            plan = strict_plan(item['content'], fixed_right_arm=True)
            executable, rejection = evaluate(plan, state, completed)
            item.update(normalized_plan=plan.model_dump(mode='json'), accepted=executable is not None, rejection=rejection)
            return plan if executable else None, rejection
        except Exception as exc:
            rejection = dict(error_code='MODEL_OR_PARSE_FAILURE', error_type=type(exc).__name__)
            item['rejection'] = rejection
            return None, rejection

    def initial(self, image_path):
        state = fixture_state()
        plan, rejection = self._request(state, image_path, False)
        if plan is None and self.recovery:
            plan, _ = self._request(state, image_path, False, rejection, self.audit[-1].get('content'))
        if plan is None:
            raise ValueError('model_initial_plan_rejected')
        return evaluate(plan, state)[0]

    def remaining(self, steps, image_path):
        # State is supported by actual release evidence, NOT predicted execution.
        state = fixture_state(completed=True)
        raw = {'actions': [dict(step_id=i+1, skill=s.source_skill, **s.args) for i, s in enumerate(steps)]}
        plan = ModelPlan.model_validate(raw)
        executable, rejection = evaluate(plan, state, completed=True)
        if executable is not None:
            return executable
        if not self.recovery:
            raise ValueError('remaining_plan_rejected_no_recovery')
        plan, _ = self._request(state, image_path, True, rejection, json.dumps(raw))
        if plan is None:
            raise ValueError('model_remaining_repair_rejected')
        return evaluate(plan, state, completed=True)[0]
