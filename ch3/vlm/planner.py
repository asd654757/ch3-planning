"""Initial structured planning and independent B0 direct planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from ch3.schema.model_plan import ModelPlan
from ch3.vlm.client import DashScopeVLMClient, VLMResponse
from ch3.vlm.parser import PlanParseError, parse_direct_plan, parse_structured_plan
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
            plan = parse_structured_plan(response.content)
            return PlanGeneration(plan, response, None, user_prompt, prompt_id, prompt_hash)
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
            plan = parse_direct_plan(response.content)
            return PlanGeneration(plan, response, None, user_prompt, prompt_id, prompt_hash)
        except PlanParseError as exc:
            return PlanGeneration(None, response, str(exc), user_prompt, prompt_id, prompt_hash)
