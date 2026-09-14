"""R0/R1/R2 repair generation and hard prefix enforcement for R2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from ch3.repair.prefix_guard import merge_locked_prefix
from ch3.schema.model_plan import ModelPlan
from ch3.state.world_state import WorldState
from ch3.validator.result import ValidationResult
from ch3.vlm.client import DashScopeVLMClient, VLMResponse
from ch3.vlm.closure import closed_world_infeasibility
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


def required_transports(
    task: Mapping[str, Any],
    state: WorldState,
) -> list[dict[str, Any]]:
    """Derive unfinished goal actions for pick/place, push and press tasks.

    This is a prompt-support field, not an oracle: it is computed only from the
    declared task goal and the already-validated prefix state.  For a held
    object, ``current_location`` is ``null`` because the object is in the hand.
    ``pushed_to`` is the canonical transport fact for push tasks; an equivalent
    ``on`` fact is not emitted again.  Press goals emit a press entry.
    """
    actions: list[dict[str, Any]] = []
    held = set(state.holding.values())
    seen_transports: set[tuple[str, str]] = set()

    def add_transport(object_id: str, target_id: str, skill: str) -> None:
        key = (object_id, target_id)
        if key in seen_transports:
            return
        seen_transports.add(key)
        actions.append(
            {
                "kind": "transport",
                "skill": skill,
                "object_id": object_id,
                "target_id": target_id,
                "currently_held": object_id in held,
                "current_location": (
                    None if object_id in held else state.at.get(object_id)
                ),
            }
        )

    for fact in task.get("goal", {}).get("facts", []):
        if not isinstance(fact, str) or not fact.endswith(")"):
            continue
        if fact.startswith("pushed_to("):
            inner = fact[len("pushed_to("):-1]
            parts = [part.strip() for part in inner.split(",", 1)]
            if len(parts) == 2:
                object_id, target_id = parts
                if object_id in state.objects and state.location_of(object_id) != target_id:
                    add_transport(object_id, target_id, "push")
        elif fact.startswith("on("):
            inner = fact[3:-1]
            parts = [part.strip() for part in inner.split(",", 1)]
            if len(parts) == 2:
                object_id, target_id = parts
                if (
                    object_id != "table"
                    and object_id in state.objects
                    and state.location_of(object_id) != target_id
                ):
                    add_transport(object_id, target_id, "place")
        elif fact.startswith("pressed("):
            object_id = fact[len("pressed("):-1]
            if object_id in state.objects and object_id not in state.pressed:
                actions.append(
                    {
                        "kind": "press",
                        "skill": "press",
                        "object_id": object_id,
                    }
                )
    return sorted(
        actions,
        key=lambda item: (
            item["kind"],
            item["object_id"],
            item.get("target_id", ""),
        ),
    )


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
        """Apply the shared deterministic closed-world rule."""
        return closed_world_infeasibility(task, plan=plan, reason=reason)

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
        if repair_mode not in {"R0", "R1", "R1_FROM_STATE", "R2"}:
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
        if repair_mode in {"R1", "R1_FROM_STATE", "R2"}:
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
        if repair_mode == "R1_FROM_STATE":
            if validation.final_state is None:
                raise ValueError(
                    "R1_FROM_STATE requires an observed prefix-final state"
                )
            # Execution-time fallback semantics: the prefix has already run,
            # so the model must not see the initial state as current and must
            # not return those executed actions again.
            prompt_input["current_state"] = sorted(
                validation.final_state.facts()
                | validation.final_state.empty_hand_facts({"left", "right"})
            )
            prompt_input.pop("original_plan", None)
            prompt_input["executed_prefix"] = [
                action.model_dump() for action in validation.validated_prefix
            ]

        if repair_mode in {"R2", "R1_FROM_STATE"} and validation.final_state is not None:
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
            prompt_input["required_transports"] = required_transports(
                task, validation.final_state
            )
            release_actions: list[dict[str, Any]] = []
            for arm, held_object in sorted(validation.final_state.holding.items()):
                first_transport = next(
                    (
                        transport
                        for transport in prompt_input["required_transports"]
                        if transport.get("object_id") == held_object
                    ),
                    None,
                )
                if first_transport is not None:
                    continue
                release_actions.append(
                    {
                        "skill": "place",
                        "object_id": held_object,
                        "target_id": "table",
                        "arm": arm,
                        "reason": (
                            f"arm {arm} is holding {held_object}, which is not "
                            "required by any remaining transport"
                        ),
                    }
                )
            if release_actions:
                prompt_input["release_actions"] = release_actions
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
                state_aware = "prefix_final_state" in prompt_input
                output_requirement = (
                    f"The first {suffix_start - 1} steps are locked. Return only the "
                    f"replacement suffix. Its first step_id must be {suffix_start}. "
                    'If the error is caused solely by actions after the locked prefix, '
                    'return exactly {"actions": []}. '
                    "If the locked prefix ends with a partial pick/place pair, "
                    "you may complete that pairing as the first suffix action; "
                    "this cross-prefix completion is allowed. "
                )
                if state_aware:
                    output_requirement += (
                        "Use prefix_final_state and held_objects to check what each "
                        "arm is currently holding; if an arm is holding an object, "
                        "it must first place that object before any pick, push, or "
                        "press. If the held object is not the object required by "
                        "the first required transport, place it on `table` first. "
                        "The program has computed required_transports from "
                        "remaining_goal_facts. If required_transports is not "
                        "empty, the replacement suffix must complete every listed "
                        "transport; do not return an empty suffix in that case. "
                        "For a place transport whose object is already held, use "
                        "only its place action; otherwise use pick immediately "
                        "followed by place. For a push transport use one push "
                        "action, and for a press entry use one press action. "
                        "Push and press are direct hand-empty skills: do not "
                        "pick the target before pushing or pressing it. After "
                        "any release action, execute the push or press directly."
                    )
                    if "release_actions" in prompt_input:
                        output_requirement += (
                            " The suffix MUST begin with exactly the actions "
                            "listed in release_actions, in order, before any "
                            "required_transports. Do not skip or reorder them."
                        )
            else:
                output_requirement = (
                    "No steps are locked because the invalid plan has no validated "
                    "prefix. Return the complete corrected plan."
                )
                if "release_actions" in prompt_input:
                    output_requirement += (
                        " The corrected plan MUST begin with exactly the actions "
                        "listed in release_actions, in order, before any "
                        "required_transports. Do not skip or reorder them."
                    )
            render_values.update(
                {
                    "mode": "R2",
                    "task_data": prompt_input,
                    "output_requirement": output_requirement,
                }
            )
        elif repair_mode == "R1_FROM_STATE":
            render_values.update(
                {
                    "mode": repair_mode,
                    "task_data": prompt_input,
                    "output_requirement": (
                        f"The first {suffix_start - 1} steps have already "
                        "executed. Return only a new remaining-task suffix; "
                        f"its first step_id must be {suffix_start}. Never "
                        "repeat any action from executed_prefix and never "
                        "return the complete original plan. Use "
                        "prefix_final_state and held_objects as the current "
                        "world state. If held_objects is nonempty, every arm "
                        "holding an object must first place it before any pick, "
                        "push, or press. If the held object is not required by "
                        "the first transport, place it on `table` first. The "
                        "returned suffix must make the merged plan satisfy every fact in "
                        "remaining_goal_facts. Returning only the immediate "
                        "place for an object already held by the prefix is "
                        "insufficient unless remaining_goal_facts becomes "
                        "empty after that suffix. Plan any additional "
                        "actions needed to finish the whole "
                        "remaining goal. The program has computed "
                        "required_transports. Complete every listed entry in "
                        "one returned suffix. For a place transport whose "
                        "object is already held, place it on target_id; "
                        "otherwise pick it and then place it on target_id. "
                        "For a push transport use one push action, and for a "
                        "press entry use one press action. Push and press are "
                        "direct hand-empty skills: do not pick the target "
                        "before pushing or pressing it. After any release "
                        "action, execute the push or press directly. Do not return "
                        "only the first required entry unless it is the only "
                        "entry."
                    ),
                }
            )
            if "release_actions" in prompt_input:
                render_values["output_requirement"] += (
                    " The returned suffix MUST begin with exactly the actions "
                    "listed in release_actions, in order, before any "
                    "required_transports. Do not skip or reorder them."
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
            if repair_mode in {"R2", "R1_FROM_STATE"} and validation.validated_prefix:
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
                reject_reason=(
                    "empty_suffix_without_validated_prefix"
                    if repair_mode == "R2"
                    else "empty_suffix"
                ),
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

        if repair_mode in {"R2", "R1_FROM_STATE"}:
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
