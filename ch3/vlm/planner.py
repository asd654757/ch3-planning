"""Initial structured planning and independent B0 direct planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from ch3.schema.model_plan import ModelPlan
from ch3.vlm.client import DashScopeVLMClient, VLMResponse
from ch3.vlm.closure import closed_world_infeasibility
from ch3.vlm.parser import (
    PlanParseError,
    parse_direct_plan_or_infeasible,
    parse_structured_plan_or_infeasible,
)
from ch3.vlm.prompts import PromptLibrary


@dataclass(frozen=True)
class PlanGeneration:
    """A parsed plan plus the raw provenance needed by collection logs."""

    plan: Optional[ModelPlan]
    response: VLMResponse
    parse_error: Optional[str] = None
    prompt: str = ""
    prompt_id: str = ""
    prompt_hash: str = ""
    infeasible_reason: Optional[str] = None

    @property
    def infeasible(self) -> bool:
        return self.infeasible_reason is not None


class InitialPlanner:
    """Generate the shared ModelPlan P for B1/B2a/B2b."""

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
    ) -> Optional[str]:
        """Apply the shared deterministic closed-world rule."""
        return closed_world_infeasibility(task, plan=plan)

    def plan(
        self,
        task: Mapping[str, Any],
        *,
        seed: Optional[int] = None,
        temperature: float = 0.7,
    ) -> PlanGeneration:
        system_prompt = (
            "You are a robotics task planner. Output only one JSON object. "
            "Do not add commentary or Markdown."
        )
        user_prompt, prompt_id, prompt_hash = self.prompts.render(
            "initial_planning",
            instruction=task["instruction"],
            objects=sorted(task["objects"]),
            goal=task["goal"],
        )
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
            plan, infeasible_reason = parse_structured_plan_or_infeasible(
                response.content
            )
            if plan is not None:
                infeasible_reason = self._closed_world_infeasibility(task, plan=plan)
                if infeasible_reason is not None:
                    plan = None
            elif infeasible_reason is None:
                infeasible_reason = self._closed_world_infeasibility(task, plan=None)
                if infeasible_reason is None:
                    infeasible_reason = "task infeasible"
            return PlanGeneration(
                plan,
                response,
                None,
                user_prompt,
                prompt_id,
                prompt_hash,
                infeasible_reason=infeasible_reason,
            )
        except PlanParseError as exc:
            return PlanGeneration(None, response, str(exc), user_prompt, prompt_id, prompt_hash)


class DirectPlanner:
    """Generate and parse the independent B0 free-text plan."""

    def __init__(
        self,
        client: DashScopeVLMClient,
        prompts: PromptLibrary | None = None,
        *,
        max_tokens: int = 1024,
        json_mode: bool = False,
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

    def plan(
        self,
        task: Mapping[str, Any],
        *,
        seed: Optional[int] = None,
        temperature: float = 0.7,
    ) -> PlanGeneration:
        system_prompt = (
            "You are a robotics task planner. Respond in concise free text; "
            "do not output JSON."
        )
        user_prompt, prompt_id, prompt_hash = self.prompts.render(
            "direct",
            instruction=task["instruction"],
            objects=sorted(task["objects"]),
            goal=task["goal"],
        )
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
            plan, infeasible_reason = parse_direct_plan_or_infeasible(response.content)
            closure_reason = self._closed_world_infeasibility(
                task, plan=plan, reason=infeasible_reason
            )
            if closure_reason is not None:
                plan, infeasible_reason = None, closure_reason
            return PlanGeneration(
                plan,
                response,
                None,
                user_prompt,
                prompt_id,
                prompt_hash,
                infeasible_reason=infeasible_reason,
            )
        except PlanParseError as exc:
            return PlanGeneration(None, response, str(exc), user_prompt, prompt_id, prompt_hash)
