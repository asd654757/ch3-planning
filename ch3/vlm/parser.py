"""Robust parsers for structured and B0 direct-text plans."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from pydantic import ValidationError

from ch3.schema.model_plan import ModelPlan


class PlanParseError(ValueError):
    """Raised when a VLM response cannot be converted to a ModelPlan."""


def parse_json_object(content: str) -> dict[str, Any]:
    """Parse a JSON object, tolerating one outer Markdown fence."""
    text = content.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as first_error:
        matches = re.findall(r"\{.*\}", text, flags=re.DOTALL)
        if not matches:
            raise PlanParseError("Response does not contain a JSON object") from first_error
        # Prefer the last match; models often add commentary before it.
        try:
            parsed = json.loads(matches[-1])
        except json.JSONDecodeError as second_error:
            raise PlanParseError("Response contains invalid JSON") from second_error
    if not isinstance(parsed, dict):
        raise PlanParseError("Response JSON must be an object")
    return parsed


def parse_structured_plan(content: str) -> ModelPlan:
    """Parse initial/R0/R1/R2 JSON output into the frozen ModelPlan schema."""
    mapping = parse_json_object(content)
    if "actions" not in mapping:
        raise PlanParseError("JSON object is missing 'actions'")
    try:
        return ModelPlan.model_validate(mapping)
    except ValidationError as exc:
        raise PlanParseError(f"ModelPlan schema validation failed: {exc}") from exc


def _normalise_arm(value: str) -> str:
    lowered = value.lower()
    if "left" in lowered or "左" in value:
        return "left"
    if "right" in lowered or "右" in value:
        return "right"
    raise PlanParseError(f"Cannot parse arm from: {value!r}")


def _clean_id(value: str) -> str:
    return value.strip().strip("`'\".,；;，。")


def parse_direct_plan(content: str) -> ModelPlan:
    """Parse B0's free-text one-action-per-line response.

    Accepted forms include:
      ``1. pick red_cube_0 with right arm``
      ``2. place red_cube_0 on tray_1 with right arm``
      ``1. 用右臂 pick red_cube_0``
      ``2. 把 red_cube_0 放到 tray_1，用右臂``

    If the model nevertheless returns JSON, we parse it too; this keeps B0 a
    "separate generation + parser" arm rather than silently discarding runs.
    """
    text = content.strip()
    if text.startswith("{") or text.startswith("```"):
        try:
            return parse_structured_plan(text)
        except PlanParseError:
            pass

    actions: list[dict[str, Any]] = []
    step_id = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^\s*(?:step\s*)?\d+[.):：]\s*", "", line, flags=re.IGNORECASE)
        line = line.lstrip("-* ").strip()
        if not line:
            continue

        arm = "right"
        arm_match = re.search(
            r"(?:(?:with|using|use)\s+)?(left|right)\s+(?:arm|hand)|用(左|右)(?:手|臂)",
            line,
            flags=re.IGNORECASE,
        )
        if arm_match:
            arm = _normalise_arm("".join(part for part in arm_match.groups() if part))

        pick_match = re.search(
            r"\b(?:pick|grasp|抓取?|拿起)\b[:：]?\s*([A-Za-z0-9_.-]+)",
            line,
            flags=re.IGNORECASE,
        )
        place_match = re.search(
            r"\b(?:place|put|put down|放到|放在|放入|放置在?)\b[:：]?\s*([A-Za-z0-9_.-]+)(?:\s+(?:on|onto|into|in|到|到|在|上|里)\s+([A-Za-z0-9_.-]+))?",
            line,
            flags=re.IGNORECASE,
        )
        if pick_match:
            step_id += 1
            actions.append(
                {
                    "step_id": step_id,
                    "skill": "pick",
                    "object_id": _clean_id(pick_match.group(1)),
                    "target_id": None,
                    "arm": arm,
                }
            )
        elif place_match:
            target = place_match.group(2)
            if not target:
                raise PlanParseError(f"Place action is missing target: {line}")
            step_id += 1
            actions.append(
                {
                    "step_id": step_id,
                    "skill": "place",
                    "object_id": _clean_id(place_match.group(1)),
                    "target_id": _clean_id(target),
                    "arm": arm,
                }
            )

    if not actions:
        raise PlanParseError("No pick/place action found in direct-plan response")
    try:
        return ModelPlan.model_validate({"actions": actions})
    except ValidationError as exc:
        raise PlanParseError(f"Direct plan schema validation failed: {exc}") from exc


def plan_to_dict(plan: ModelPlan) -> dict[str, Any]:
    """Return a JSON-compatible dict with stable field order."""
    return json.loads(plan.model_dump_json())
