"""Hard prefix-preservation guard for one-shot R2 repair.

The VLM may propose a suffix, but it never owns the validated prefix.  This
module performs the program-side merge and rejects any response that tries to
overlap, omit, reorder, or renumber the locked prefix.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from ch3.schema.model_plan import ModelPlan, ModelPlanAction
from ch3.validator.syntax_validator import check_plan


@dataclass(frozen=True)
class PrefixGuardResult:
    """Result of program-side prefix enforcement."""

    accepted: bool
    merged_plan: Optional[ModelPlan] = None
    reject_reason: Optional[str] = None


def merge_locked_prefix(
    prefix: Sequence[ModelPlanAction], returned_plan: ModelPlan
) -> PrefixGuardResult:
    """Merge a validated prefix with a model-proposed suffix.

    Rules:
    - The validated prefix must already be contiguous from step 1.
    - If a prefix exists, the model must return a non-empty suffix.
    - The model may not return any prefix step (even if identical).
    - Suffix step IDs must be exactly ``len(prefix)+1, len(prefix)+2, ...``.
    - The returned plan is deep-copied before merging.
    """
    prefix_actions = list(prefix)
    prefix_ids = [action.step_id for action in prefix_actions]

    if prefix_actions and prefix_ids != list(range(1, len(prefix_actions) + 1)):
        return PrefixGuardResult(
            accepted=False,
            reject_reason="validated_prefix_not_contiguous_from_one",
        )

    returned_ids = [action.step_id for action in returned_plan.actions]

    if not prefix_actions:
        # With no validated prefix, R2 degrades to a full-plan replacement.
        bad_step, _message = check_plan(returned_plan)
        if bad_step is not None:
            return PrefixGuardResult(
                accepted=False,
                reject_reason="replacement_step_ids_not_contiguous_from_one",
            )
        return PrefixGuardResult(
            accepted=True,
            merged_plan=returned_plan.model_copy(deep=True),
        )

    expected_start = len(prefix_actions) + 1
    if not returned_ids:
        return PrefixGuardResult(accepted=False, reject_reason="empty_suffix")

    prefix_id_set = set(prefix_ids)
    if any(step_id in prefix_id_set for step_id in returned_ids):
        return PrefixGuardResult(accepted=False, reject_reason="prefix_overlap")

    if returned_ids[0] != expected_start:
        return PrefixGuardResult(accepted=False, reject_reason="suffix_start_mismatch")

    expected_ids = list(range(expected_start, expected_start + len(returned_ids)))
    if returned_ids != expected_ids:
        return PrefixGuardResult(
            accepted=False,
            reject_reason="suffix_step_ids_not_consecutive",
        )

    merged_actions = [action.model_copy(deep=True) for action in prefix_actions]
    merged_actions.extend(
        action.model_copy(deep=True) for action in returned_plan.actions
    )
    return PrefixGuardResult(
        accepted=True,
        merged_plan=ModelPlan(actions=merged_actions),
    )
