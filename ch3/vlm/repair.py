"""R0/R1/R2 repair generation and hard prefix enforcement for R2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from ch3.repair.prefix_guard import merge_locked_prefix
from ch3.schema.model_plan import ModelPlan
from ch3.state.world_state import WorldState
from ch3.validator.result import ValidationResult
from ch3.vlm.client import DashScopeVLMClient, VLMResponse
from ch3.vlm.parser import PlanParseError, parse_structured_plan
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
            render_values.update(
                {
                    "mode": "R2",
                    "task_data": prompt_input,
                    "output_requirement": (
                        f"The first {suffix_start - 1} steps are locked. Return only the "
                        f"replacement suffix. Its first step_id must be {suffix_start}."
                    ),
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

        try:
            returned_plan = parse_structured_plan(response.content)
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
