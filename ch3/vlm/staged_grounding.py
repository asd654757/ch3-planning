"""Separate image localization from semantic grounding; no evaluator inputs."""

import json
from typing import Annotated

from pydantic import Field, model_validator

from ch3.vlm.scene_grounding import (
    Entity, GroundedScene, GroundingParseError, Relation, StrictRecord,
)


Coordinate = Annotated[float, Field(ge=0, le=1000)]


class ImageBox(StrictRecord):
    x_min: Coordinate
    y_min: Coordinate
    x_max: Coordinate
    y_max: Coordinate

    @model_validator(mode="after")
    def ordered(self):
        if self.x_min >= self.x_max or self.y_min >= self.y_max:
            raise ValueError("box must have positive width and height")
        return self

    def normalized(self):
        return tuple(x / 1000 for x in (self.x_min, self.y_min, self.x_max, self.y_max))


class LocatedObject(StrictRecord):
    object_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    box: ImageBox


class Localization(StrictRecord):
    coordinate_system: str = Field(pattern="^image_0_1000$")
    objects: list[LocatedObject]
    uncertainty: list[str]

    def entities(self):
        return [Entity(object_id=o.object_id, description=o.description,
                       confidence=o.confidence, bbox=o.box.normalized()) for o in self.objects]


class Semantics(StrictRecord):
    observed: list[Relation]
    goal: list[Relation]
    needs_observation: bool
    uncertainty: list[str]


class StagedGrounder:
    def __init__(self, client):
        self.client = client

    def localize(self, *, image_path, seed):
        response = self.client.complete(
            system_prompt=("Locate all visible manipulable objects and destination surfaces, "
                           "including distractors. Exclude robot arms, table and background. "
                           "Assign local IDs. Boxes use image_0_1000 coordinates, origin top left. "
                           "Boxes must tightly enclose the entire visible object, not its center. "
                           "Do not treat floating colored goal markers as physical objects. "
                           "Use four NAMED scalar fields, not arrays. Return schema JSON only. "
                           "Report uncertainty rather than guessing. uncertainty must be [] "
                           "when no uncertainty exists; never put 'no uncertainty' in the list."),
            user_prompt=json.dumps({"output_schema": Localization.model_json_schema()}),
            image_path=image_path, seed=seed, temperature=0.1, max_tokens=1600, json_mode=True,
        )
        try:
            result = Localization.model_validate_json(response.content)
            GroundedScene(entities=result.entities(), observed=[], goal=[],
                          needs_observation=False, uncertainty=[]).visual_bindings()
        except ValueError as exc:
            raise GroundingParseError(str(exc), response) from exc
        return result, response

    def interpret(self, *, localization, instruction, image_path, arms, skills, seed):
        response = self.client.complete(
            system_prompt=("Parse the instruction into goal relations and estimate current "
                           "relations from the image. Use ONLY supplied local entity IDs and "
                           "EXACT arm IDs. Do not invent objects or change localization. "
                           "holding: arm, object; hand_empty: arm, null; on/pushed_to: object, "
                           "surface; pressed: object, null. table is reserved. Desired goals "
                           "are not observations. Missing or ambiguous hand/location evidence "
                           "requires needs_observation=true. Account for every supplied entity's "
                           "location, including distractors, only when supported by the image. "
                           "Each arm is either empty OR holding one object, never both. "
                           "Do not emit zero-confidence placeholder relations. If the hand is "
                           "not visible, omit its relation and request observation; do not guess. "
                           "uncertainty must be [] if none; otherwise describe actual missing "
                           "evidence, not reassuring text. Return only schema JSON."),
            user_prompt=json.dumps({"instruction": instruction,
                                    "localization": localization.model_dump(mode="json"),
                                    "available_arms": arms, "available_skills": skills,
                                    "output_schema": Semantics.model_json_schema()}),
            image_path=image_path, seed=seed, temperature=0.1, max_tokens=1600, json_mode=True,
        )
        try:
            result = Semantics.model_validate_json(response.content)
        except ValueError as exc:
            raise GroundingParseError(str(exc), response) from exc
        scene = GroundedScene(entities=localization.entities(), observed=result.observed,
                              goal=result.goal, needs_observation=result.needs_observation,
                              uncertainty=localization.uncertainty + result.uncertainty)
        return scene, response
