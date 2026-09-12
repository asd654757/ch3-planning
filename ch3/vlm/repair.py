"""R0/R1/R2 repair generation and hard prefix enforcement for R2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from ch3.goal.infeasibility import logical_conflict_infeasibility
from ch3.repair.prefix_guard import merge_locked_prefix
from ch3.schema.model_plan import ModelPlan
from ch3.state.world_state import WorldState
from ch3.validator.result import ValidationResult
from ch3.vlm.client import DashScopeVLMClient, VLMResponse
from ch3.vlm.parser import (
    PlanParseError,
    parse_json_object,
    parse_structured_plan_or_infeasible,
)
from ch3.vlm.planner import PlanGeneration
from ch3.vlm.prompts import PromptLibrary


@dataclass(frozen=True)
class RepairGeneration:
    """One repair call. ``accepted`` is false if parsing or R2 check failed."""

    repair_mode: str
    plan: Optional[ModelPlan]
    response: VLMResponse
    accepted: bool
    reject_reason: Optional[str] = None
    parse_error: Optional[str] = None
    prompt: str = ""
    prompt_id: str = ""
    prompt_hash: str = ""
    locked_prefix_step_ids: tuple[int, ...] = ()
    merged_with_prefix: bool = False
    infeasible_reason: Optional[str] = None

    @property
    def infeasible(self) -> bool:
        return self.infeasible_reason is not None


class PlanRepairer:
    """Implements the frozen one-call repair protocol."""

    def __init__(
        self,
        client: DashScopeVLMClient,
        prompts: PromptLibrary | None = None,
        *,
        max_tokens: int = 1024,
        json_mode: bool = True,
    ) -> None:
        self.client = client
        self.prompts = prompts or PromptLibrary()
        self.max_tokens = max_tokens
        self.json_mode = json_mode

    @staticmethod
    def _closed_world_infeasibility(
        task: Mapping[str, Any],
        *,
        plan: Optional[ModelPlan],
        reason: Optional[str] = None,
    ) -> Optional[str]:
        """Apply the same deterministic closure rule to repair responses."""
        visible = set(map(str, task["objects"]))
        conflict_reason = logical_conflict_infeasibility(task)
        if conflict_reason is not None:
            return conflict_reason
        if plan is not None:
            for action in plan.actions:
                for value in (action.object_id, action.target_id):
                    if value and value != "table" and value not in visible:
                        return f"object {value} is not in the visible list"
        if reason is not None:
            return reason

        for fact in task.get("goal", {}).get("facts", []):
            if not isinstance(fact, str) or not fact.startswith("on(") or not fact.endswith(")"):
                continue
            inner = fact[3:-1]
            parts = [part.strip() for part in inner.split(",", 1)]
            if len(parts) == 2:
                for object_id in parts:
                    if object_id != "table" and object_id not in visible:
                        return f"goal object {object_id} is not in the visible list"
        return None

    def repair(
        self,
        *,
        repair_mode: str,
        task: Mapping[str, Any],
        initial_generation: PlanGeneration,
        validation: ValidationResult,
        initial_state: WorldState,
        seed: Optional[int] = None,
        temperature: float = 0.3,
    ) -> RepairGeneration:
        if repair_mode not in {"R0", "R1", "R2"}:
            raise ValueError(f"Unknown repair mode: {repair_mode}")
        system_prompt = (
            "You are a robotics plan repairer. Output only one JSON object. "
            "Do not add commentary or Markdown."
        )
        prompt_input: dict[str, Any] = {
            "instruction": task["instruction"],
            "objects": sorted(task["objects"]),
            "goal": task["goal"],
            "current_state": sorted(
                initial_state.facts() | initial_state.empty_hand_facts({"left", "right"})
            ),
            "original_plan": initial_generation.plan.model_dump() if initial_generation.plan else None,
        }
        if repair_mode in {"R1", "R2"}:
            prompt_input.update(
                {
                    "first_invalid_step": validation.first_invalid_step,
                    "error_code": validation.error_code.value if validation.error_code else None,
                    "error_layer": validation.layer,
                    "error_message": validation.message,
                    "validated_prefix": [
                        action.model_dump() for action in validation.validated_prefix
                    ],
                }
            )
        if repair_mode == "R2" and validation.final_state is not None:
            # State-aware suffix repair: report the world state *after* the
            # validated prefix executes, so the model knows what the arms are
            # holding before generating the replacement suffix.  All derived
            # fields are computed programmatically; the model must not infer
            # them itself.
            final_facts = (
                validation.final_state.facts()
                | validation.final_state.empty_hand_facts({"left", "right"})
            )
            prompt_input["prefix_final_state"] = sorted(final_facts)
            prompt_input["held_objects"] = dict(
                sorted(validation.final_state.holding.items())
            )
            goal_facts = [
                f for f in task.get("goal", {}).get("facts", [])
                if isinstance(f, str)
            ]
            prompt_input["remaining_goal_facts"] = [
                f for f in goal_facts if f not in final_facts
            ]
            prompt_input["next_step_id"] = len(validation.validated_prefix) + 1
        if repair_mode == "R0":
            # Frozen: no error localization and no validated prefix leakage.
            prompt_input.pop("original_plan", None)
            prompt_input.pop("current_state", None)

        suffix_start = len(validation.validated_prefix) + 1
        render_values: dict[str, Any] = {
            "instruction": task["instruction"],
            "objects": sorted(task["objects"]),
            "goal": task["goal"],
        }
        if repair_mode == "R2":
            if validation.validated_prefix:
                output_requirement = (
                    f"The first {suffix_start - 1} steps are locked. Return only the "
                    f"replacement suffix. Its first step_id must be {suffix_start}. "
                    'If the error is caused solely by actions after the locked prefix, '
                    'return exactly {"actions": []}. '
                    "If the locked prefix ends with a pick, you may complete its "
                    "pairing with a place as the first suffix action; this "
                    "cross-prefix pick/place pairing is allowed even though "
                    "constraint 5 applies inside a complete plan. "
                    "Use prefix_final_state and held_objects to check what each "
                    "arm is currently holding; an arm holding an object must "
                    "place it before picking a new one."
                )
            else:
                output_requirement = (
                    "No steps are locked because the invalid plan has no validated "
                    "prefix. Return the complete corrected plan."
                )
            render_values.update(
                {
                    "mode": "R2",
                    "task_data": prompt_input,
                    "output_requirement": output_requirement,
                }
            )
        else:
            render_values.update(
                {
                    "mode": repair_mode,
                    "task_data": prompt_input,
                    "output_requirement": "Return the complete replacement plan.",
                }
            )
        user_prompt, prompt_id, prompt_hash = self.prompts.render("repair", **render_values)

        response = self.client.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_path=task.get("image_path"),
            temperature=temperature,
            max_tokens=self.max_tokens,
            seed=seed,
            json_mode=self.json_mode,
        )

        # A valid complete prefix plus an illegal suffix is correctly repaired
        # by deleting that suffix.  ModelPlan intentionally forbids an empty
        # full plan, so R2 treats an explicit empty suffix as a no-op merge.
        try:
            empty_probe = parse_json_object(response.content)
        except PlanParseError:
            empty_probe = None
        if (
            empty_probe is not None
            and empty_probe.get("status") is None
            and empty_probe.get("actions") == []
        ):
            if repair_mode == "R2" and validation.validated_prefix:
                merged_plan = ModelPlan(
                    actions=[
                        action.model_copy(deep=True)
                        for action in validation.validated_prefix
                    ]
                )
                closure_reason = self._closed_world_infeasibility(
                    task, plan=merged_plan, reason=None
                )
                if closure_reason is not None:
                    return RepairGeneration(
                        repair_mode,
                        None,
                        response,
                        accepted=True,
                        prompt=user_prompt,
                        prompt_id=prompt_id,
                        prompt_hash=prompt_hash,
                        locked_prefix_step_ids=tuple(
                            action.step_id for action in validation.validated_prefix
                        ),
                        merged_with_prefix=True,
                        infeasible_reason=closure_reason,
                    )
                return RepairGeneration(
                    repair_mode,
                    merged_plan,
                    response,
                    accepted=True,
                    prompt=user_prompt,
                    prompt_id=prompt_id,
                    prompt_hash=prompt_hash,
                    locked_prefix_step_ids=tuple(
                        action.step_id for action in validation.validated_prefix
                    ),
                    merged_with_prefix=True,
                )
            return RepairGeneration(
                repair_mode,
                None,
                response,
                accepted=False,
                reject_reason="empty_suffix_without_validated_prefix"
                if repair_mode == "R2"
                else "empty_plan",
                parse_error="ModelPlan cannot be empty",
                prompt=user_prompt,
                prompt_id=prompt_id,
                prompt_hash=prompt_hash,
            )

        try:
            returned_plan, infeasible_reason = parse_structured_plan_or_infeasible(
                response.content
            )
        except PlanParseError as exc:
            return RepairGeneration(
                repair_mode,
                None,
                response,
                accepted=False,
                reject_reason="parse_error",
                parse_error=str(exc),
                prompt=user_prompt,
                prompt_id=prompt_id,
                prompt_hash=prompt_hash,
            )

        closure_reason = self._closed_world_infeasibility(
            task, plan=returned_plan, reason=infeasible_reason
        )
        if closure_reason is not None:
            return RepairGeneration(
                repair_mode,
                None,
                response,
                accepted=True,
                prompt=user_prompt,
                prompt_id=prompt_id,
                prompt_hash=prompt_hash,
                infeasible_reason=closure_reason,
            )

        if returned_plan is None:
            return RepairGeneration(
                repair_mode,
                None,
                response,
                accepted=True,
                prompt=user_prompt,
                prompt_id=prompt_id,
                prompt_hash=prompt_hash,
                infeasible_reason=infeasible_reason,
            )

        if repair_mode == "R2":
            guard = merge_locked_prefix(
                validation.validated_prefix, returned_plan
            )
            if not guard.accepted:
                return RepairGeneration(
                    repair_mode,
                    None,
                    response,
                    accepted=False,
                    reject_reason=guard.reject_reason,
                    prompt=user_prompt,
                    prompt_id=prompt_id,
                    prompt_hash=prompt_hash,
                    locked_prefix_step_ids=tuple(
                        action.step_id for action in validation.validated_prefix
                    ),
                )
            return RepairGeneration(
                repair_mode,
                guard.merged_plan,
                response,
                accepted=True,
                prompt=user_prompt,
                prompt_id=prompt_id,
                prompt_hash=prompt_hash,
                locked_prefix_step_ids=tuple(
                    action.step_id for action in validation.validated_prefix
                ),
                merged_with_prefix=bool(validation.validated_prefix),
            )

        return RepairGeneration(
            repair_mode,
            returned_plan,
            response,
            accepted=True,
            prompt=user_prompt,
            prompt_id=prompt_id,
            prompt_hash=prompt_hash,
        )
