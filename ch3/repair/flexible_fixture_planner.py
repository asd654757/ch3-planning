"""Constraint-based model planning for the existing two-cube execution fixture.

Unlike the legacy exact-sequence gate, accepts any supported adjacent pick/place
transfers satisfying current goals and protected-object constraints.
"""
import hashlib
import json
from copy import deepcopy

from ch3.compiler.executable_plan import compile_plan
from ch3.execution.observed_continuation import fixed_registry
from ch3.repair.persistent_model_recovery import strict_plan
from ch3.repair.sequence_model_planner import OBJECTS
from ch3.schema.model_plan import ModelPlan
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator

MOVABLE = {'blue_candidate', 'yellow_candidate'}
REGIONS = {'green_region', 'return_region'}


def diagnostic_feedback(rejection):
    """Project local rejection evidence into a bounded, non-state diagnostic.

    Full candidates/predictions stay in the audit. Never forward simulated
    locations, raw model text or a subset of goals as the next planning task.
    """
    if not rejection:
        return None
    code = rejection.get('error_code', 'REJECTED')
    result = dict(error_code=code, candidate_executed=False,
        role='diagnostic_only_not_current_state_or_replacement_goal',
        repair_mode='regenerate_complete_suffix_from_current_task')
    if code == 'GOAL_NOT_SATISFIED':
        result.update(category='incomplete_goal_coverage',
            omitted_goal_count=len(rejection.get('unmet_goal_facts', [])),
            correction='Cover ALL current remaining obligations together, not just a missing-goal patch.')
    elif code == 'PROTECTED_OBJECT_MUTATION':
        result.update(category='protected_object_violation',
            correction='Do not act on forbidden_objects; regenerate the complete remaining plan.')
    elif code == 'MODEL_OR_PARSE_FAILURE':
        result.update(category='output_or_request_failure',
            correction='Return a JSON object with actions matching output_schema; no bare array.')
    else:
        result.update(category='plan_contract_violation',
            correction='Recheck current facts, object registry and backend contract for the complete suffix.')
    return result


def transfer_pairs(steps):
    """Backend contract, not a prescribed answer/order. Never rewrite a plan."""
    if len(steps) % 2 or len(steps) > 8:
        raise ValueError('unsupported_transfer_count')
    pairs = []
    for pick, place in zip(steps[::2], steps[1::2]):
        obj = pick.args['object_id']
        if (pick.source_skill != 'pick' or place.source_skill != 'place'
                or obj not in MOVABLE or place.args['object_id'] != obj
                or place.args.get('target_id') not in REGIONS):
            raise ValueError('unsupported_backend_transfer')
        pairs.append((obj, place.args['target_id']))
    return pairs


def evaluate_flexible(plan, state, goals, protected=()):
    registry = fixed_registry()
    validation = Validator(OBJECTS, registry, pick_surfaces=REGIONS|{'observed_support'}).validate(plan, state)
    if not validation.valid:
        return None, dict(error_code=str(validation.error_code), message=validation.message)
    if any(a.object_id in set(protected) for a in plan.actions):
        return None, dict(error_code='PROTECTED_OBJECT_MUTATION')
    if any(a.object_id not in MOVABLE for a in plan.actions):
        return None, dict(error_code='NON_MOVABLE_OBJECT')
    required = {*(f'on({obj}, {region})' for obj,region in goals.items()), 'hand_empty(right)'}
    predicted = validation.final_state.facts() | validation.final_state.empty_hand_facts({'right'})
    if not required <= predicted:
        return None, dict(error_code='GOAL_NOT_SATISFIED',
            unmet_goal_facts=sorted(required-predicted),
            predicted_final_facts=sorted(predicted),
            evidence_source='candidate_symbolic_simulation_not_simulator_truth')
    executable = compile_plan(plan, registry)
    try:
        transfer_pairs(executable.steps)
    except ValueError as exc:
        return None, dict(error_code='BACKEND_CONTRACT', message=str(exc))
    return executable, None


class FlexibleFixturePlanner:
    flexible = True
    long_task = False

    def __init__(self, client, *, recovery, shared, update_goals=False, error_feedback=True):
        self.client, self.recovery, self.shared = client, recovery, shared
        self.error_feedback = error_feedback
        self.audit = []
        self.state = WorldState.table_scene(OBJECTS)
        self.history = []
        self.goals = dict(blue_candidate='return_region', yellow_candidate='green_region')
        self.update_goals = update_goals
        self.goal_updates = []

    def protected(self):
        completed_objects = {obj for obj,_ in self.history}
        return {obj for obj in completed_objects if self.state.location_of(obj)==self.goals[obj]}

    def observe_release(self, obj, region):
        # Caller may only advance after detachment AND observed goal support.
        self.state.at[obj] = region
        self.state.holding.pop('right', None)
        self.history.append((obj,region))
        if self.update_goals and len(self.history)==1:
            old = dict(self.goals)
            self.goals = dict(blue_candidate='green_region', yellow_candidate='return_region')
            self.goal_updates.append(dict(event='user_goal_update_after_first_supported_delivery', old_goals=old,
                new_goals=dict(self.goals), source='predeclared_controlled_task_update_not_execution_failure'))

    def _request(self, image_path, *, feedback=None, initial=False):
        if not initial and sum(a['phase']!='initial' for a in self.audit)>=2:
            raise ValueError('remaining_repair_call_budget_exhausted')
        prompt = dict(instruction='Deliver each registered cube to its CURRENT requested region. '
            'Choose a legal transfer order. Preserve objects already satisfying their CURRENT goal. '
            'Executed history is not a command to replay; changed goals may require moving a previously delivered object. '
            'Return the COMPLETE replacement suffix satisfying ALL remaining_goal_facts from current_facts. '
            'Rejected candidates were NEVER executed. Do not return only an incremental patch. '
            'The current task below is authoritative; feedback is diagnostic only. '
            'Before returning actions, internally check every required obligation and the empty-hand goal.',
            current_facts=sorted(self.state.facts()|self.state.empty_hand_facts({'right'})),
            state_source='controlled_initial_fixture' if initial else 'supported_RGB_release_history_plus_untouched_fixture',
            goals=dict(self.goals), executed_history=list(self.history), goal_updates=deepcopy(self.goal_updates),
            remaining_goal_facts=sorted(f'on({obj}, {region})' for obj,region in self.goals.items()
                if self.state.location_of(obj)!=region),
            current_task_obligations=[dict(object_id=obj,observed_location=self.state.location_of(obj),
                required_target=region,requires_delivery=self.state.location_of(obj)!=region)
                for obj,region in sorted(self.goals.items())],
            terminal_conditions=['hand_empty(right)'],
            forbidden_objects=sorted(self.protected()), movable_objects=sorted(MOVABLE), targets=sorted(REGIONS),
            backend_contract='right arm, adjacent pick/place of same cube; up to four transfers; end empty-handed',
            feedback=diagnostic_feedback(feedback) if self.error_feedback else None, output_schema={'actions':[dict(step_id='contiguous from 1',skill='pick or place',
                object_id='registered cube',target_id='registered region for place, omitted for pick')]})
        item=dict(request=prompt, local_feedback_evidence=deepcopy(feedback),
            feedback_protocol='authoritative_task_diagnostic_projection_v6',
            image_sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(),
            actual_model_calls=0, accepted=False, phase=('initial' if initial else
                'remaining_task_repair' if self.history else 'initial_plan_correction'))
        self.audit.append(item)
        try:
            if initial and 'flexible_initial' in self.shared:
                saved=self.shared['flexible_initial']
                if saved['request']!=prompt or saved['image_sha256']!=item['image_sha256']:
                    raise ValueError('shared_initial_input_mismatch')
                item.update(content=saved['content'], total_tokens=0, shared_candidate=True)
            else:
                item['actual_model_calls']=1
                response=self.client.complete(system_prompt='Return only a JSON OBJECT with the top-level key "actions". '
                    'Never return a bare array. Satisfy the current task and backend contract.',
                    user_prompt=json.dumps(prompt),image_path=image_path,temperature=.1,seed=0,max_tokens=1024,json_mode=False)
                item.update(content=response.content,total_tokens=response.total_tokens)
                if initial:
                    self.shared['flexible_initial']=dict(request=deepcopy(prompt),image_sha256=item['image_sha256'],content=response.content)
            plan=strict_plan(item['content'],fixed_right_arm=True)
            executable,rejection=evaluate_flexible(plan,self.state,self.goals,self.protected())
            if rejection is not None:
                rejection['rejected_candidate']=plan.model_dump(mode='json')
            item.update(accepted=executable is not None,rejection=rejection,normalized_plan=plan.model_dump(mode='json'))
            return executable
        except Exception as exc:
            item['rejection']=dict(error_code='MODEL_OR_PARSE_FAILURE',error_type=type(exc).__name__)
            if 'content' in item:
                item['rejection']['rejected_output']=item['content']
            if isinstance(exc, ValueError) and str(exc)=='shared_initial_input_mismatch':
                item['rejection']['local_rejection_code']='shared_initial_input_mismatch'
            return None

    def initial(self,image_path):
        executable=self._request(image_path,initial=True)
        if executable is None and self.recovery:
            executable=self._request(image_path,feedback=self.audit[-1]['rejection'])
        if executable is None:
            raise ValueError('flexible_initial_plan_rejected')
        return executable

    def _remaining_request(self, image_path, rejection):
        # Identical noninitial budget and acceptance gate in both settings.
        # The direct-replan reference retries without validator error content.
        while sum(a['phase']!='initial' for a in self.audit)<2:
            executable=self._request(image_path,feedback=rejection)
            if executable is not None:
                return executable
            rejection=self.audit[-1]['rejection']
        raise ValueError('flexible_remaining_repair_rejected')

    def remaining(self,steps,image_path,*,completed):
        if completed != len(self.history):
            raise ValueError('executed_history_progress_mismatch')
        if all(self.state.location_of(obj)==region for obj,region in self.goals.items()):
            from ch3.compiler.executable_plan import ExecutablePlan
            return ExecutablePlan()
        raw={'actions':[dict(step_id=i+1,skill=s.source_skill,**s.args) for i,s in enumerate(steps)]}
        if steps:
            plan=ModelPlan.model_validate(raw)
            executable,rejection=evaluate_flexible(plan,self.state,self.goals,self.protected())
            if rejection is not None:
                rejection['rejected_candidate']=plan.model_dump(mode='json')
        else:
            executable,rejection=None,dict(error_code='REMAINING_GOAL_WITH_EMPTY_SUFFIX')
        if executable is not None:
            return executable
        if not self.recovery:
            raise ValueError('remaining_plan_rejected_no_recovery')
        return self._remaining_request(image_path,rejection)
