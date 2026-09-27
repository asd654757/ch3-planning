"""Conservative recovery from an observed post-execution state.

The default transition callback is symbolic and must not be reported as physical
execution. A real adapter must supply independent post-action observations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ch3.goal.goal_checker import goal_satisfied
from ch3.schema.model_plan import GoalSpec, ModelPlan, ModelPlanAction
from ch3.state.simulator import step
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator


Observer = Callable[[WorldState, ModelPlanAction], WorldState | None]


@dataclass
class RecoveryOutcome:
    accepted: bool
    reason: str
    actions: list[ModelPlanAction] = field(default_factory=list)
    final_state: WorldState | None = None


def recover_from_observation(
    *,
    observed_state: WorldState,
    goal: GoalSpec,
    validator: Validator,
    allowed_skills: set[str],
    observe_after_action: Observer | None = None,
    symbolic_only: bool = True,
    max_actions: int = 8,
) -> RecoveryOutcome:
    """Return and verify only a suffix; executed prefix is never replayed.

    A held decoy is released to the registered table only when it is not part
    of any remaining goal. The observer must return a *new* independent state
    after each action in an online application. A mismatch stops the sequence.
    """
    from scripts.symbolic_planner_baseline import BFS_VALID, search_suffix

    if not symbolic_only and observe_after_action is None:
        return RecoveryOutcome(False, "online_observation_required")
    state = observed_state.copy()
    if state.objects != validator.scene_objects or not set(state.holding).issubset(validator.registry.arms):
        return RecoveryOutcome(False, "untrusted_observation")
    if len(set(state.holding.values())) != len(state.holding) or any(
        obj not in state.objects for obj in state.holding.values()
    ):
        return RecoveryOutcome(False, "untrusted_observation")
    if goal_satisfied(state, goal, validator.registry.arms):
        return RecoveryOutcome(True, "goal_already_satisfied", final_state=state)

    releases: list[dict] = []
    if state.holding:
        if state.table_id not in validator.valid_targets or not validator.registry.has_skill("place"):
            return RecoveryOutcome(False, "no_safe_release_target")
        for arm, obj in sorted(state.holding.items()):
            # A release that changes a goal object is not a safe generic rule.
            if any(f"({obj}," in fact or f"({obj})" in fact for fact in goal.facts):
                continue
            releases.append({"skill": "place", "object_id": obj, "target_id": state.table_id, "arm": arm})

    planning_state = state.copy()
    for spec in releases:
        action = ModelPlanAction(step_id=1, **spec)
        planning_state, ok, _, _ = step(planning_state, action, validator.valid_targets)
        if not ok:
            return RecoveryOutcome(False, "unsafe_release")
    remaining = max_actions - len(releases)
    if remaining < 0:
        return RecoveryOutcome(False, "action_budget_exceeded")
    if goal_satisfied(planning_state, goal, validator.registry.arms):
        suffix: list[dict] = []
    else:
        found, suffix, _ = search_suffix(
            state=planning_state,
            goal=goal,
            valid_targets=validator.valid_targets,
            allowed_skills=(allowed_skills | {"place"}) & validator.registry.skills,
            mode=BFS_VALID,
            max_actions=remaining,
            arms=validator.registry.arms,
        )
        if not found:
            return RecoveryOutcome(False, "no_valid_suffix")

    actions = [
        ModelPlanAction.model_validate({**spec, "step_id": index})
        for index, spec in enumerate([*releases, *suffix], start=1)
    ]
    if actions:
        checked = validator.validate(ModelPlan(actions=actions), state)
        if not checked.valid or not goal_satisfied(checked.final_state, goal, validator.registry.arms):
            return RecoveryOutcome(False, "candidate_failed_validation")

    executed: list[ModelPlanAction] = []
    for action in actions:
        predicted, ok, _, _ = step(state, action, validator.valid_targets)
        if not ok:
            return RecoveryOutcome(False, "step_precondition_failed", executed, state)
        actual = observe_after_action(state.copy(), action) if observe_after_action else predicted
        if actual is None:
            return RecoveryOutcome(False, "missing_feedback", executed, state)
        executed.append(action)
        state = actual.copy()
        if (state.objects != validator.scene_objects or state.facts() != predicted.facts()
                or state.empty_hand_facts(validator.registry.arms)
                != predicted.empty_hand_facts(validator.registry.arms)):
            return RecoveryOutcome(False, "observation_mismatch", executed, state)
    if not goal_satisfied(state, goal, validator.registry.arms):
        return RecoveryOutcome(False, "goal_not_observed", executed, state)
    return RecoveryOutcome(True, "observed_goal_satisfied", executed, state)
