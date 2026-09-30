"""Ground instructions and images without simulator state or goal truth."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ch3.schema.model_plan import GoalSpec
from ch3.state.world_state import WorldState
from ch3.vlm.client import DashScopeVLMClient, VLMResponse


class GroundingParseError(ValueError):
    def __init__(self, message: str, response: VLMResponse):
        super().__init__(message)
        self.response = response


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Entity(StrictRecord):
    object_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    bbox: tuple[
        float, float, float, float
    ] | None = Field(None, description="Normalized image box; EACH coordinate between 0 and 1, NOT pixels or 0-1000.")


class Relation(StrictRecord):
    predicate: Literal["on", "holding", "hand_empty", "pressed", "pushed_to"]
    subject: str
    target: str | None = None
    confidence: float = Field(ge=0, le=1)


class GroundedScene(StrictRecord):
    entities: list[Entity]
    observed: list[Relation]
    goal: list[Relation]
    needs_observation: bool
    uncertainty: list[str]

    def visual_bindings(self) -> dict[str, tuple[float, float, float, float]]:
        """Return image-space regions, not simulator body handles."""
        bindings = {}
        for entity in self.entities:
            if entity.bbox is None:
                raise ValueError("missing visual region")
            x0, y0, x1, y1 = entity.bbox
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                raise ValueError("invalid normalized visual region")
            if entity.object_id in bindings:
                raise ValueError("duplicate visual identifier")
            if entity.bbox in bindings.values():
                raise ValueError("ambiguous identical visual regions")
            bindings[entity.object_id] = entity.bbox
        return bindings

    def to_planning_input(
        self, *, arms: set[str], threshold: float = 0.85,
    ) -> tuple[WorldState, GoalSpec]:
        """Reject unknowns instead of treating missing facts as empty hands."""
        if not 0 <= threshold <= 1:
            raise ValueError("confidence threshold must be in [0, 1]")
        if self.needs_observation or self.uncertainty or not self.goal:
            raise ValueError("additional observation or instruction clarification required")
        ids = [e.object_id for e in self.entities]
        if len(ids) != len(set(ids)) or set(ids) & (arms | {"table"}):
            raise ValueError("duplicate or reserved entity identifier")
        if any(e.confidence < threshold for e in self.entities):
            raise ValueError("uncertain entity")
        objects = set(ids)
        state = WorldState(objects=objects)
        empty: set[str] = set()
        goal_facts: list[str] = []
        seen: set[tuple[str, str, str | None]] = set()
        for is_goal, relations in ((False, self.observed), (True, self.goal)):
            for relation in relations:
                p, s, t = relation.predicate, relation.subject, relation.target
                if relation.confidence < threshold:
                    raise ValueError("uncertain relation")
                if p in {"holding", "hand_empty"}:
                    if s not in arms:
                        raise ValueError("unknown arm")
                elif s not in objects:
                    raise ValueError("unknown object reference")
                if p == "holding" and t not in objects:
                    raise ValueError("unknown held object")
                if p in {"on", "pushed_to"} and (t not in objects | {"table"} or t == s):
                    raise ValueError("invalid surface reference")
                if p in {"hand_empty", "pressed"} and t is not None:
                    raise ValueError("unary relation has a target")
                if is_goal:
                    goal_facts.append(f"{p}({s})" if t is None else f"{p}({s}, {t})")
                    continue
                key = (p, s, t)
                if key in seen:
                    raise ValueError("duplicate observation")
                seen.add(key)
                if p == "hand_empty":
                    empty.add(s)
                elif p == "holding":
                    if s in state.holding:
                        raise ValueError("multiple objects held by one arm")
                    state.holding[s] = t
                elif p in {"on", "pushed_to"}:
                    if s in state.at and state.at[s] != t:
                        raise ValueError("conflicting object locations")
                    state.at[s] = t
                    if p == "pushed_to":
                        state.pushed.add(s)
                elif p == "pressed":
                    state.pressed.add(s)
        held = list(state.holding.values())
        if len(held) != len(set(held)) or empty & set(state.holding):
            raise ValueError("conflicting hand observations")
        if set(held) & set(state.at):
            raise ValueError("object is both held and resting on a surface")
        if empty | set(state.holding) != arms:
            raise ValueError("unobserved arm state")
        if set(state.at) | set(held) != objects:
            raise ValueError("unobserved object location")
        return state, GoalSpec(facts=goal_facts)


class SceneGrounder:
    def __init__(self, client: DashScopeVLMClient) -> None:
        self.client = client

    def ground(
        self, *, instruction: str, image_path: str | Path,
        skills: list[str], arms: list[str], seed: int,
    ) -> tuple[GroundedScene, VLMResponse]:
        # An allowlisted API prevents task dictionaries from leaking truth.
        prompt = json.dumps({
            "instruction": instruction, "available_skills": skills,
            "available_arms": arms,
            "output_schema": GroundedScene.model_json_schema(),
        }, ensure_ascii=False)
        response = self.client.complete(
            system_prompt=(
                "Infer task goals from the instruction and current state ONLY from the image. "
                "Assign local entity IDs and use them consistently. These IDs are not executor "
                "bindings. For every entity provide bbox=[x_min,y_min,x_max,y_max], "
                "normalized to [0,1] with origin at the image top left. "
                "Example bbox: [0.2,0.3,0.4,0.5], never [200,300,400,500]. "
                "Use EXACT available_arms identifiers as subjects for hand relations. "
                "Robot arms are not entities. Include all visible manipulable objects and "
                "target surfaces, including distractors. "
                "Do not infer that instructed actions already happened. table is the "
                "reserved support surface. holding uses arm as subject and object as target; "
                "hand_empty uses arm as subject; on/pushed_to use object and surface; pressed "
                "uses object as subject. Report confidence honestly. If hand state, object "
                "identity, location or instruction is ambiguous, set needs_observation=true "
                "and describe uncertainty. Return only JSON matching the supplied schema."
            ), user_prompt=prompt, image_path=image_path, seed=seed,
            temperature=0.1, max_tokens=2048, json_mode=True,
        )
        try:
            scene = GroundedScene.model_validate_json(response.content)
        except ValueError as exc:
            raise GroundingParseError(str(exc), response) from exc
        return scene, response
