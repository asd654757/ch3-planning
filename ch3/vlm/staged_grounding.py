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


class ObservationAnswer(StrictRecord):
    """A bounded answer to a machine-generated missing-evidence query."""

    observed: list[Relation]
    resolved: list[str]
    unresolved: list[str]
    uncertainty: list[str]

    @model_validator(mode="after")
    def exclusive_resolution(self):
        if self.resolved and self.unresolved:
            raise ValueError("a single observation answer cannot be resolved and unresolved")
        return self


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
                           "evidence, not reassuring text. If the image contains labeled views, "
                           "they show the SAME scene: do not count each view as new objects. "
                           "Local entity boxes refer to the original localization view, not "
                           "the combined image. Use other views to check the same entities and "
                           "hand state. Return only schema JSON."),
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

    def observe_missing(self, *, localization, instruction, image_path, requests,
                        arms, seed):
        """Ask only for listed missing facts; never let the model rewrite localization."""
        response = self.client.complete(
            system_prompt=(
                "Answer only the listed visual-observation requests for the SAME scene. "
                "Use only supplied local IDs and exact arm IDs. Do not invent IDs, goals, "
                "or actions. A request is resolved only when the image provides evidence. "
                "For an unseen or ambiguous fact, put its exact request string in unresolved "
                "and explain the missing evidence. Do not guess an empty hand. Each arm is "
                "either hand_empty or holding one object, never both. Return schema JSON only."
            ),
            user_prompt=json.dumps({"instruction": instruction,
                                    "localization": localization.model_dump(mode="json"),
                                    "requests": requests, "available_arms": arms,
                                    "output_schema": ObservationAnswer.model_json_schema()}),
            image_path=image_path, seed=seed, temperature=0.1, max_tokens=1200, json_mode=True,
        )
        try:
            answer = ObservationAnswer.model_validate_json(response.content)
            unknown = set(answer.unresolved)
            if unknown - set(requests) or set(answer.resolved) - set(requests):
                raise ValueError("observation answer references an undeclared request")
        except ValueError as exc:
            raise GroundingParseError(str(exc), response) from exc
        return answer, response

    def observe_one(self, *, localization, instruction, image_path, request,
                    arms, seed):
        """Run the same bounded protocol for exactly one missing fact."""
        answer, response = self.observe_missing(
            localization=localization, instruction=instruction, image_path=image_path,
            requests=[request], arms=arms, seed=seed,
        )
        if answer.resolved and answer.resolved != [request]:
            raise GroundingParseError("single observation resolved the wrong request", response)
        if answer.unresolved and answer.unresolved != [request]:
            raise GroundingParseError("single observation unresolved the wrong request", response)
        return answer, response


def missing_observation_requests(scene: GroundedScene, *, arms: set[str]) -> list[str]:
    """Derive bounded queries from returned facts; truth is never consulted."""
    objects = {entity.object_id for entity in scene.entities}
    observed = {(item.predicate, item.subject, item.target) for item in scene.observed}
    requests = [f"location:{object_id}" for object_id in sorted(objects)
                if not any(p in {"on", "pushed_to", "holding"} and s == object_id
                           for p, s, _ in observed)]
    for arm in sorted(arms):
        if not any((p == "hand_empty" and s == arm) or (p == "holding" and s == arm)
                   for p, s, _ in observed):
            requests.append(f"hand_state:{arm}")
    return requests


def merge_observation(scene: GroundedScene, answer: ObservationAnswer) -> GroundedScene:
    """Merge only returned observations; unresolved evidence keeps the scene unsafe."""
    # A relation is accepted only when its request was explicitly resolved. The
    # answer schema currently uses request strings, so unresolved answers are
    # retained for audit but their extra relations cannot silently complete state.
    accepted = answer.observed if not answer.unresolved else []
    return GroundedScene(entities=scene.entities, observed=scene.observed + accepted,
                         goal=scene.goal,
                         needs_observation=bool(answer.unresolved or answer.uncertainty),
                         uncertainty=scene.uncertainty + answer.uncertainty + answer.unresolved)


def validate_observation_relation(answer: ObservationAnswer, request: str) -> None:
    """Ensure a resolved answer contains a relation matching its declared query."""
    if answer.unresolved:
        return
    if answer.resolved != [request] or not answer.observed:
        raise ValueError("resolved observation must contain the requested relation")
    kind, _, identifier = request.partition(":")
    relation = answer.observed
    if kind == "location" and not any(item.subject == identifier and
                                      item.predicate in {"on", "pushed_to", "holding"}
                                      for item in relation):
        raise ValueError("location answer does not identify the requested object")
    if kind == "hand_state" and not any(item.subject == identifier and
                                        item.predicate in {"hand_empty", "holding"}
                                        for item in relation):
        raise ValueError("hand answer does not identify the requested arm")
