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
LONG_INSTRUCTION = ('First move the blue cube to the green region as an intermediate delivery. '
    'After releasing it there, pick it up again and move it to the magenta return region. '
    'Only then move the yellow cube to the green region. Do not skip the intermediate delivery. '
    'Leave blue untouched after its return-region delivery and finish empty-handed.')


def transfers(long_task=False):
    pairs = [('blue_candidate', 'return_region'), ('yellow_candidate', 'green_region')]
    return [('blue_candidate', 'green_region')] + pairs if long_task else pairs


def fixture_state(completed=False, *, long_task=False):
    state = WorldState.table_scene(OBJECTS)
    for obj, region in transfers(long_task)[:int(completed)]:
        state.at[obj] = region
    return state


def evaluate(plan, state, completed=False, *, long_task=False):
    registry = fixed_registry()
    result = Validator(OBJECTS, registry, pick_surfaces={'green_region', 'return_region'} if long_task else None).validate(plan, state)
    goal = GoalSpec(facts=['on(blue_candidate, return_region)', 'on(yellow_candidate, green_region)', 'hand_empty(right)'])
    if not result.valid:
        return None, dict(error_code=str(result.error_code), message=result.message)
    if not goal_satisfied(result.final_state, goal, {'right'}):
        return None, dict(error_code='GOAL_NOT_SATISFIED', remaining_goal_facts=sorted(
            set(goal.facts) - (result.final_state.facts() | result.final_state.empty_hand_facts({'right'}))))
    # Explicit executor scope, never silently replace unsupported model plans.
    expected = [action for obj, region in transfers(long_task)[int(completed):]
                for action in [('pick', obj, None), ('place', obj, region)]]
    actual = [(a.skill.value, a.object_id, a.target_id) for a in plan.actions]
    if actual != expected:
        return None, dict(error_code='BACKEND_SEQUENCE_OUT_OF_SCOPE', supported_order=expected)
    return compile_plan(plan, registry), None


class SequenceModelPlanner:
    def __init__(self, client, *, recovery, shared, long_task=False):
        self.client, self.recovery, self.shared = client, recovery, shared
        self.audit = []
        self.long_task = long_task

    def _request(self, state, image_path, completed, feedback=None, previous=None):
        final_goal = {'on(blue_candidate, return_region)', 'on(yellow_candidate, green_region)', 'hand_empty(right)'}
        history = [f'{skill} {obj}' + (f' {region}' if skill == 'place' else '')
                   for obj, region in transfers(self.long_task)[:int(completed)] for skill in ('pick', 'place')]
        prompt = dict(instruction=LONG_INSTRUCTION if self.long_task else INSTRUCTION,
            current_facts=sorted(state.facts() | state.empty_hand_facts({'right'})),
            state_source='controlled_initial_fixture' if not completed else 'RGB_supported_latest_release_plus_prior_supported_and_untouched_fixture_facts',
            objects=sorted(OBJECTS), targets=['return_region', 'green_region'],
            remaining_goal_facts=sorted(final_goal - (state.facts() | state.empty_hand_facts({'right'}))),
            executed_history=history,
            completed_transfer_count=int(completed),
            forbidden_objects=['blue_candidate'] if state.at.get('blue_candidate') == 'return_region' else [],
            backend_configuration={'arm': 'right', 'skills': ['pick', 'place']},
            pick_support_surfaces=['table', 'green_region', 'return_region'] if self.long_task else ['table'],
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
            executable, rejection = evaluate(plan, state, completed, long_task=self.long_task)
            item.update(normalized_plan=plan.model_dump(mode='json'), accepted=executable is not None, rejection=rejection)
            return plan if executable else None, rejection
        except Exception as exc:
            rejection = dict(error_code='MODEL_OR_PARSE_FAILURE', error_type=type(exc).__name__)
            item['rejection'] = rejection
            return None, rejection

    def initial(self, image_path):
        state = fixture_state(long_task=self.long_task)
        plan, rejection = self._request(state, image_path, False)
        if plan is None and self.recovery:
            plan, _ = self._request(state, image_path, False, rejection, self.audit[-1].get('content'))
        if plan is None:
            raise ValueError('model_initial_plan_rejected')
        return evaluate(plan, state, long_task=self.long_task)[0]

    def remaining(self, steps, image_path, *, completed=1):
        # State is supported by actual release evidence, NOT predicted execution.
        state = fixture_state(completed=completed, long_task=self.long_task)
        raw = {'actions': [dict(step_id=i+1, skill=s.source_skill, **s.args) for i, s in enumerate(steps)]}
        plan = ModelPlan.model_validate(raw)
        executable, rejection = evaluate(plan, state, completed=completed, long_task=self.long_task)
        if executable is not None:
            return executable
        if not self.recovery:
            raise ValueError('remaining_plan_rejected_no_recovery')
        plan, _ = self._request(state, image_path, completed, rejection, json.dumps(raw))
        if plan is None:
            raise ValueError('model_remaining_repair_rejected')
        return evaluate(plan, state, completed=completed, long_task=self.long_task)[0]

    def execution_repair(self, steps, image_path, *, completed, holding_object=None, event,
                         observed_object=None):
        """A real model call from externally supported CURRENT state, not predicted pick."""
        state = fixture_state(completed=completed, long_task=self.long_task)
        if observed_object is not None:
            if observed_object not in {'blue_candidate', 'yellow_candidate'} or holding_object:
                raise ValueError('unsupported observed recovery state')
            # Physical support observation is not semantic target satisfaction.
            # Never label an off-target object "on(goal_region)" or invent table support.
            state.at[observed_object] = 'observed_support'
        if holding_object:
            state.holding['right'] = holding_object
        expected = [(s.source_skill, s.args['object_id'], s.args.get('target_id')) for s in steps]
        request = dict(instruction=LONG_INSTRUCTION if self.long_task else INSTRUCTION,
            current_facts=sorted(state.facts() | state.empty_hand_facts({'right'})),
            current_state_source=event['state_source'], latest_execution_event=event,
            completed_transfers=transfers(self.long_task)[:completed],
            remaining_goal_facts=sorted({'on(blue_candidate, return_region)', 'on(yellow_candidate, green_region)',
                'hand_empty(right)'} - (state.facts() | state.empty_hand_facts({'right'}))),
            objects=sorted(OBJECTS), targets=['green_region', 'return_region'],
            pick_support_surfaces=['table', 'green_region', 'return_region', 'observed_support'],
            output_schema={'actions': [{'step_id': 'contiguous from 1', 'skill': 'pick or place',
                'object_id': 'registered object', 'target_id': 'required for place only'}]},
            backend_arm='right', forbidden_objects=['blue_candidate'] if state.at.get('blue_candidate')=='return_region' else [],
            rejection_feedback=event)
        item = dict(request=request, actual_model_calls=1, accepted=False, phase='execution_repair',
            image_sha256=hashlib.sha256(image_path.read_bytes()).hexdigest())
        self.audit.append(item)
        try:
            response = self.client.complete(system_prompt='Return only JSON with actions. Repair ONLY the remaining task from current supported facts. History is not current state. Never repeat completed transfers. If already holding an object, place it before any pick.',
                user_prompt=json.dumps(request), image_path=image_path, seed=0, temperature=.1, max_tokens=1024, json_mode=False)
            item.update(content=response.content, total_tokens=response.total_tokens)
            plan = strict_plan(response.content, fixed_right_arm=True)
            registry = fixed_registry()
            check = Validator(OBJECTS, registry, pick_surfaces={'green_region', 'return_region', 'observed_support'}).validate(plan, state)
            goal = GoalSpec(facts=['on(blue_candidate, return_region)', 'on(yellow_candidate, green_region)', 'hand_empty(right)'])
            actual = [(a.skill.value, a.object_id, a.target_id) for a in plan.actions]
            item.update(normalized_plan=plan.model_dump(mode='json'), valid=check.valid,
                        goal_satisfied=bool(check.valid and goal_satisfied(check.final_state, goal, {'right'})),
                        backend_scope_satisfied=actual==expected)
            if not (item['valid'] and item['goal_satisfied'] and item['backend_scope_satisfied']):
                raise ValueError('execution_repair_rejected')
            item['accepted'] = True
            return compile_plan(plan, registry)
        except Exception as exc:
            item['rejection'] = dict(error_type=type(exc).__name__)
            raise ValueError('model_execution_repair_rejected') from None
