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


def diagnostic_feedback(rejection, *, execution_contract=True):
    """Project local rejection evidence into a bounded, non-state diagnostic.

    Full candidates/predictions stay in the audit. Never forward simulated
    locations, raw model text or a subset of goals as the next planning task.
    """
    if not rejection:
        return None
    code = rejection.get('error_code', 'REJECTED')
    if execution_contract and code == 'OBSERVED_GOAL_NOT_SATISFIED':
        return dict(error_code=code, event_type='execution_event', action_executed=True,
            category='observed_postcondition_failure',
            evidence_source='supported_RGB_release_and_goal_check',
            attempted_action=deepcopy(rejection.get('attempted_action')),
            observation=deepcopy(rejection.get('observation')),
            correction='Plan from the observed state and current goals; check occupied destinations and remaining budget.')
    result = dict(error_code=code, candidate_executed=False,
        event_type='candidate_rejection',
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
    elif code == 'DESTINATION_OCCUPIED':
        result.update(category='placement_prerequisite_failure',
            violated_precondition=dict(object_id=rejection.get('object_id'),
                target_id=rejection.get('target_id'),
                occupying_objects=deepcopy(rejection.get('occupying_objects', []))),
            correction='Check current destination_occupancy and clear occupying cubes before placing; regenerate the entire suffix.')
    elif code == 'REMAINING_EXECUTION_BUDGET':
        result.update(category='remaining_budget_violation',
            correction='The entire suffix must fit execution_budget.remaining_transfers; do not replay successful deliveries.')
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


def evaluate_flexible(plan, state, goals, protected=(), *, execution_contract=False, remaining_transfers=4):
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
        pairs = transfer_pairs(executable.steps)
    except ValueError as exc:
        return None, dict(error_code='BACKEND_CONTRACT', message=str(exc))
    if execution_contract:
        if len(pairs) > remaining_transfers:
            return None, dict(error_code='REMAINING_EXECUTION_BUDGET',
                required_transfers=len(pairs), remaining_transfers=remaining_transfers)
        locations = {obj: state.location_of(obj) for obj in MOVABLE}
        for index, (obj, target) in enumerate(pairs):
            occupants = sorted(other for other, location in locations.items()
                if other != obj and location == target)
            if occupants:
                return None, dict(error_code='DESTINATION_OCCUPIED', transfer_index=index,
                    object_id=obj, target_id=target, occupying_objects=occupants,
                    evidence_source='current_observed_facts_and_candidate_projection_not_simulator_truth')
            locations[obj] = target
    return executable, None


class FlexibleFixturePlanner:
    flexible = True
    long_task = False

    def __init__(self, client, *, recovery, shared, update_goals=False, error_feedback=True,
                 update_on_deviation=False, execution_contract=False, max_tokens=1024,
                 enable_thinking=None, execution_diagnostics=False, compact_feedback=False):
        if max_tokens <= 0:
            raise ValueError('max_tokens must be positive')
        self.max_tokens = max_tokens
        self.enable_thinking = enable_thinking
        self.execution_diagnostics = execution_diagnostics
        self.compact_feedback = compact_feedback
        self.client, self.recovery, self.shared = client, recovery, shared
        self.error_feedback = error_feedback
        self.audit = []
        self.state = WorldState.table_scene(OBJECTS)
        self.history = []
        self.goals = dict(blue_candidate='return_region', yellow_candidate='green_region')
        self.update_goals = update_goals
        self.update_on_deviation = update_on_deviation
        self.goal_updates = []
        self.execution_deviations = []
        self.execution_contract = execution_contract
        self.attempted_transfers = 0

    def set_execution_progress(self, attempted_transfers):
        if attempted_transfers < self.attempted_transfers or not 0 <= attempted_transfers <= 4:
            raise ValueError('invalid_execution_progress')
        self.attempted_transfers = attempted_transfers

    def _evaluate(self, plan):
        return evaluate_flexible(plan, self.state, self.goals, self.protected(),
            execution_contract=self.execution_contract,
            remaining_transfers=4-self.attempted_transfers)

    def planning_snapshot(self, feedback=None):
        """One authoritative state, with rejected candidates isolated from observations."""
        rejection = None
        if self.error_feedback and feedback and feedback.get('rejected_candidate'):
            rejection = dict(candidate_executed=False,
                candidate=deepcopy(feedback['rejected_candidate']),
                violations={k:deepcopy(v) for k,v in feedback.items()
                    if k not in {'rejected_candidate','predicted_final_facts','rejected_output'}})
        result = dict(protocol='execution_contract_v8',
            task='Generate a COMPLETE remaining plan, not a patch to an earlier plan.',
            objects=[dict(id=obj, location=self.state.location_of(obj), goal=self.goals[obj],
                protected=obj in self.protected()) for obj in sorted(MOVABLE)],
            state_source='controlled_fixture_updated_by_supported_RGB_release',
            hand='empty' if not self.state.holding else dict(self.state.holding),
            regions=sorted(REGIONS),
            rules=['Each transfer is adjacent pick(object) then place(same object, target).',
                'Before placing, no OTHER cube may occupy that region. Evaluate occupancy after each transfer.',
                'All objects must finish at their listed CURRENT goals and the hand must be empty.',
                'Never move protected objects. The candidate below was not executed and is not current state.'],
            budget=dict(remaining_transfers=4-self.attempted_transfers,
                max_actions=2*(4-self.attempted_transfers)),
            # Shared raw evidence survives the contract snapshot projection.
            # Diagnostics may summarize it, but may not give one method more facts.
            recovery_evidence=dict(
                protocol='shared_recovery_evidence_v1',
                supported_deliveries=deepcopy(self.history),
                goal_updates=deepcopy(self.goal_updates),
                execution_deviations=deepcopy(self.execution_deviations),
                local_feedback={k: deepcopy(v) for k, v in (feedback or {}).items()
                    if k not in {'predicted_final_facts', 'rejected_output'}},
                semantics='Deliveries and deviations are observed execution evidence. '
                    'Rejected candidates were not executed; predictions are not observations. '
                    'Past deliveries do not certify the current goal after a goal update.'),
            rejected_candidate_review=rejection,
            output_schema={'actions':[dict(step_id='integer starting at 1',skill='pick or place',
                object_id='object id',target_id='region id for place; omit for pick')]})
        if self.execution_diagnostics:
            result['protocol'] = 'execution_contract_v9_diagnostic_ablation'
            result['execution_diagnostic'] = (
                diagnostic_feedback(feedback) if self.error_feedback and feedback
                and feedback.get('error_code') == 'OBSERVED_GOAL_NOT_SATISFIED' else None)
        # Keep the common task/state contract identical, including initial
        # prompts. Only diagnostics differ; old targets never become commands.
        if self.compact_feedback and self.error_feedback and feedback:
            if feedback.get('error_code') == 'OBSERVED_GOAL_NOT_SATISFIED':
                result['execution_diagnostic'] = dict(
                    event='released_off_goal', state_already_updated=True,
                    directive='Use current object goals, not the historical failed target. No failed-object priority.')
            elif rejection:
                result['rejected_candidate_review'] = dict(
                    candidate_executed=False,
                    error_code=feedback.get('error_code'),
                    constraint_details={k: deepcopy(feedback[k]) for k in (
                        'transfer_index', 'object_id', 'target_id', 'occupying_objects',
                        'unmet_goal_facts', 'required_transfers', 'remaining_transfers') if k in feedback},
                    directive='Regenerate the complete suffix from unchanged current observations.')
        return result

    def choose_goal_observation(self, image_path, *, evidence, remaining_observations, observation_context=None):
        """Acquire evidence, not actions: no stale state or truth is supplied here.

        Uses the SAME noninitial call budget as remaining-plan generation. The
        evidence-only schema deliberately cannot assert task completion.
        """
        if evidence.get('status') != 'unknown' or remaining_observations != 1:
            raise ValueError('unsupported_goal_observation_request')
        if sum(a['phase'] != 'initial' for a in self.audit) >= 2:
            return 'stop'
        context = observation_context or dict(stage='post_release_goal',
            available_observation='Repeat open-hand retreat probe and capture two fresh RGB frames.')
        prompt = dict(task='Choose observe or stop for unresolved observation evidence.',
            observation_context=deepcopy(context),
            evidence=deepcopy(evidence), remaining_observations=remaining_observations,
            executed_history=deepcopy(self.history),
            history_scope='Past supported deliveries, not a certificate of the unresolved current goal.',
            current_goals=deepcopy(self.goals),
            available_observation=context['available_observation'],
            limitations='Observation may remain unknown; do not assume task success or infer a location.',
            output_schema={'decision': 'observe or stop'})
        if self.compact_feedback:
            prompt['summary'] = dict(observation_status='unknown', completion_authorized=False,
                instruction='Acquire evidence before committing to completion or a recovery action.')
        item = dict(phase='observation_decision', request=deepcopy(prompt),
            inference_protocol='observation_json_object_v2',
            image_sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(),
            actual_model_calls=1, accepted=False)
        self.audit.append(item)
        try:
            options = {} if self.enable_thinking is None else {'enable_thinking': self.enable_thinking}
            response = self.client.complete(system_prompt='Return only a JSON object with decision.',
                user_prompt=json.dumps(prompt), image_path=image_path, temperature=.1,
                seed=0, max_tokens=self.max_tokens, json_mode=True, **options)
            item.update(content=response.content, total_tokens=response.total_tokens,
                response_model=getattr(response, 'model', None),
                request_id=getattr(response, 'request_id', None),
                latency_ms=getattr(response, 'latency_ms', None))
            parsed = json.loads(response.content)
            if set(parsed) != {'decision'} or parsed['decision'] not in {'observe', 'stop'}:
                raise ValueError('invalid_observation_decision')
            item.update(accepted=True, decision=parsed['decision'])
            return parsed['decision']
        except Exception as exc:
            item['error'] = type(exc).__name__ + ': ' + str(exc)
            return 'stop'

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
            state_source=('controlled_initial_fixture' if initial else
                'supported_RGB_release_and_off_goal_observation_plus_untouched_fixture' if self.execution_deviations
                else 'supported_RGB_release_history_plus_untouched_fixture'),
            observed_execution_deviations=[dict(object_id=e['object_id'],status='detached_off_goal',
                location='observed_support') for e in self.execution_deviations],
            goals=dict(self.goals), executed_history=list(self.history), goal_updates=deepcopy(self.goal_updates),
            remaining_goal_facts=sorted(f'on({obj}, {region})' for obj,region in self.goals.items()
                if self.state.location_of(obj)!=region),
            current_task_obligations=[dict(object_id=obj,observed_location=self.state.location_of(obj),
                required_target=region,requires_delivery=self.state.location_of(obj)!=region)
                for obj,region in sorted(self.goals.items())],
            terminal_conditions=['hand_empty(right)'],
            forbidden_objects=sorted(self.protected()), movable_objects=sorted(MOVABLE), targets=sorted(REGIONS),
            backend_contract='right arm, adjacent pick/place of same cube; up to four transfers; end empty-handed',
            feedback=diagnostic_feedback(feedback, execution_contract=self.execution_contract) if self.error_feedback else None, output_schema={'actions':[dict(step_id='contiguous from 1',skill='pick or place',
                object_id='registered cube',target_id='registered region for place, omitted for pick')]})
        if self.execution_contract:
            prerequisites = [dict(action='place',object_id=obj,target_id=target,
                requires=dict(region_clear_of=other),
                reason='other registered cube currently occupies this placement region')
                for obj,target in sorted(self.goals.items()) for other in sorted(MOVABLE)
                if other != obj and self.state.location_of(other)==target]
            prompt.update(execution_budget=dict(max_total_transfers=4,
                attempted_transfers=self.attempted_transfers,
                remaining_transfers=4-self.attempted_transfers,
                unit='one transfer is TWO actions: pick then place, not one action',
                remaining_action_capacity=2*(4-self.attempted_transfers),
                remaining_model_calls=2-sum(a['phase']!='initial' for a in self.audit)),
                destination_occupancy={region:sorted(obj for obj in MOVABLE
                    if self.state.location_of(obj)==region) for region in sorted(REGIONS)},
                action_prerequisites=prerequisites,
                placement_precondition='Destination must be clear of other registered cubes. '
                    'Move an occupying cube away before placing there. Generate the order yourself; '
                    'the entire replacement suffix must fit the remaining transfer budget.',
                occupancy_scope='latest_supported_release_history_not_continuous_scene_certificate')
            prompt['instruction'] += (' A completed entry in executed_history does NOT mean the cube '
                'satisfies a changed current goal. Use requires_delivery for BOTH cubes. '
                'Each required delivery uses one pick AND one place: two remaining transfers permit '
                'four actions. Do not confuse transfer count with action count.')
            prompt['instruction'] += (' HARD RULE: before any place action, inspect action_prerequisites. '
                'A destination occupied by another cube is unavailable even if it is this cube\'s goal. '
                'First satisfy that prerequisite through legal actions; check again after each proposed transfer. '
                'The currently failed cube does not automatically have priority. Return only executable actions '
                'covering every requires_delivery obligation; no explanation or imagined successful execution.')
        if self.execution_contract:
            prompt = self.planning_snapshot(feedback)
        item=dict(request=prompt, local_feedback_evidence=deepcopy(feedback),
            feedback_protocol=('execution_contract_v8' if self.execution_contract
                else 'authoritative_task_diagnostic_projection_v6'),
            image_sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(),
            actual_model_calls=0, accepted=False, phase=('initial' if initial else
                'execution_repair' if self.execution_deviations else
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
                options = {} if self.enable_thinking is None else {'enable_thinking': self.enable_thinking}
                response=self.client.complete(system_prompt='Return only a JSON OBJECT with the top-level key "actions". '
                    'Never return a bare array. Satisfy the current task and backend contract.',
                    user_prompt=json.dumps(prompt),image_path=image_path,temperature=.1,seed=0,max_tokens=self.max_tokens,json_mode=False,**options)
                # Preserve provenance needed to distinguish a real model call from
                # a replay/shared candidate in comparative experiments.  These
                # fields are metadata only and are never shown to the planner.
                item.update(content=response.content,total_tokens=response.total_tokens,
                    response_model=getattr(response, 'model', None),
                    response_id=getattr(response, 'response_id', None),
                    request_id=getattr(response, 'request_id', None),
                    latency_ms=getattr(response, 'latency_ms', None),
                    finish_reason=getattr(response, 'finish_reason', None))
                if initial:
                    self.shared['flexible_initial']=dict(request=deepcopy(prompt),image_sha256=item['image_sha256'],content=response.content)
            plan=strict_plan(item['content'],fixed_right_arm=True)
            executable,rejection=self._evaluate(plan)
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

    def released_object_repair(self, image_path, *, object_id, release, observed_goal):
        """Recover only a positively detached, stationary off-goal object.

        No completed-delivery history is added. Unknown evidence cannot authorize
        recovery; observed_support is deliberately not a fabricated destination.
        """
        if (object_id not in MOVABLE or release.get('status')!='release_supported'
                or observed_goal.get('status')!='not_satisfied'):
            raise ValueError('unsupported_released_object_recovery_evidence')
        self.state.at[object_id]='observed_support'
        self.state.holding.pop('right',None)
        event=dict(object_id=object_id,release=deepcopy(release),observed_goal=deepcopy(observed_goal),
            attempted_action=dict(skill='place',object_id=object_id,target_id=self.goals[object_id]),
            state_source='RGB_release_and_stationary_off_goal_on_assumed_plane')
        self.execution_deviations.append(event)
        if self.update_on_deviation and not self.goal_updates:
            old=dict(self.goals)
            self.goals=dict(blue_candidate='green_region',yellow_candidate='return_region')
            self.goal_updates.append(dict(event='user_goal_update_after_supported_execution_deviation',
                old_goals=old,new_goals=dict(self.goals),
                source='predeclared_controlled_task_update_not_failure_inference'))
        if not self.recovery:
            raise ValueError('execution_deviation_no_recovery')
        return self._remaining_request(image_path,dict(error_code='OBSERVED_GOAL_NOT_SATISFIED',
            attempted_action=event['attempted_action'],
            observation=dict(release_status=release['status'],goal_status=observed_goal['status'],
                object_id=object_id,location='observed_support')))

    def remaining(self,steps,image_path,*,completed):
        if completed != len(self.history):
            raise ValueError('executed_history_progress_mismatch')
        if all(self.state.location_of(obj)==region for obj,region in self.goals.items()):
            from ch3.compiler.executable_plan import ExecutablePlan
            return ExecutablePlan()
        raw={'actions':[dict(step_id=i+1,skill=s.source_skill,**s.args) for i,s in enumerate(steps)]}
        if steps:
            plan=ModelPlan.model_validate(raw)
            executable,rejection=self._evaluate(plan)
            if rejection is not None:
                rejection['rejected_candidate']=plan.model_dump(mode='json')
        else:
            executable,rejection=None,dict(error_code='REMAINING_GOAL_WITH_EMPTY_SUFFIX')
        if executable is not None:
            return executable
        if not self.recovery:
            raise ValueError('remaining_plan_rejected_no_recovery')
        return self._remaining_request(image_path,rejection)
