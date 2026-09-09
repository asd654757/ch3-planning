from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ch3.capability.registry import CapabilityRegistry
from ch3.schema.model_plan import ModelPlan, ModelPlanAction
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator
from ch3.vlm.client import VLMResponse, image_data_url, response_from_raw
from ch3.vlm.collector import (
    collect_one,
    load_scenarios,
    world_state_from_task,
)
from ch3.vlm.mock import MockVLMClient
from ch3.vlm.parser import PlanParseError, parse_direct_plan, parse_structured_plan
from ch3.vlm.planner import DirectPlanner, InitialPlanner
from ch3.vlm.prompts import PromptLibrary
from ch3.vlm.repair import PlanRepairer


SCENARIO = {
    "task_id": "test_task",
    "difficulty": "easy",
    "instruction": "Put the red cube into the tray.",
    "objects": ["red_cube_0", "tray_1"],
    "initial_state": {
        "at": {"red_cube_0": "table", "tray_1": "table"},
        "holding": {},
    },
    "goal": {"facts": ["on(red_cube_0, tray_1)"]},
    "image_path": None,
}

VALID_PLAN = {
    "actions": [
        {
            "step_id": 1,
            "skill": "pick",
            "object_id": "red_cube_0",
            "target_id": None,
            "arm": "right",
        },
        {
            "step_id": 2,
            "skill": "place",
            "object_id": "red_cube_0",
            "target_id": "tray_1",
            "arm": "right",
        },
    ]
}


def test_parse_structured_plan() -> None:
    content = "```json\n" + json.dumps(VALID_PLAN) + "\n```"
    plan = parse_structured_plan(content)
    assert [action.skill.value for action in plan.actions] == ["pick", "place"]


def test_parse_direct_plan_aliases() -> None:
    plan = parse_direct_plan(
        "1. pick red_cube_0 with right arm\n"
        "2. put red_cube_0 on tray_1 with right arm"
    )
    assert plan.actions[0].object_id == "red_cube_0"
    assert plan.actions[1].target_id == "tray_1"
    assert plan.actions[1].arm.value == "right"


def test_initial_planner_uses_mock_and_prompt_hash() -> None:
    client = MockVLMClient(VALID_PLAN)
    planner = InitialPlanner(client, PromptLibrary())
    generation = planner.plan(SCENARIO, seed=3)
    assert generation.plan is not None
    assert generation.plan.actions[0].object_id == "red_cube_0"
    assert len(generation.prompt_hash) == 64
    assert "Visible/closed-world scene object IDs" in generation.prompt
    assert client.payloads[0]["seed"] == 3


def test_image_data_url(tmp_path: Path) -> None:
    image = tmp_path / "tiny.png"
    image.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000d4944415478da63f8cfc0f01f0005000105a52ca40000000049454e44ae426082"
        )
    )
    assert image_data_url(image).startswith("data:image/png;base64,")


def test_response_from_raw_usage() -> None:
    raw = {
        "id": "resp_1",
        "model": "mock-vlm",
        "choices": [
            {
                "message": {"content": '{"actions":[]}'},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 7, "completion_tokens": 9, "total_tokens": 16},
    }
    response = response_from_raw(raw, "fallback", 12)
    assert response.response_id == "resp_1"
    assert (response.prompt_tokens, response.completion_tokens) == (7, 9)


def _registry_and_state() -> tuple[Validator, WorldState]:
    registry = CapabilityRegistry.from_yaml("config/capability_registry.yaml")
    state = world_state_from_task(SCENARIO)
    validator = Validator(scene_objects=set(SCENARIO["objects"]), registry=registry)
    return validator, state


def _action(
    step_id: int, skill: str, object_id: str, target_id: str | None, arm: str
) -> ModelPlanAction:
    return ModelPlanAction(
        step_id=step_id,
        skill=skill,
        object_id=object_id,
        target_id=target_id,
        arm=arm,
    )


def test_r2_rejects_returned_prefix_overlap() -> None:
    validator, state = _registry_and_state()
    broken = ModelPlan(
        actions=[
            _action(1, "pick", "red_cube_0", None, "right"),
            _action(2, "pick", "red_cube_0", None, "right"),
        ]
    )
    validation = validator.validate(broken, state)
    assert not validation.valid
    client = MockVLMClient(VALID_PLAN)  # starts at step_id 1, so must be rejected
    repairer = PlanRepairer(client)
    generation = PlanGenerationForTest(broken)
    repaired = repairer.repair(
        repair_mode="R2",
        task=SCENARIO,
        initial_generation=generation,
        validation=validation,
        initial_state=state,
        seed=1,
    )
    assert not repaired.accepted
    assert repaired.reject_reason == "prefix_overlap"


def test_r2_merges_locked_prefix_with_valid_suffix() -> None:
    validator, state = _registry_and_state()
    broken = ModelPlan(
        actions=[
            _action(1, "pick", "red_cube_0", None, "right"),
            _action(2, "pick", "red_cube_0", None, "right"),
        ]
    )
    validation = validator.validate(broken, state)
    suffix = {
        "actions": [
            {
                "step_id": 2,
                "skill": "place",
                "object_id": "red_cube_0",
                "target_id": "tray_1",
                "arm": "right",
            }
        ]
    }
    repairer = PlanRepairer(MockVLMClient(suffix))
    repaired = repairer.repair(
        repair_mode="R2",
        task=SCENARIO,
        initial_generation=PlanGenerationForTest(broken),
        validation=validation,
        initial_state=state,
        seed=1,
    )
    assert repaired.accepted
    assert repaired.plan is not None
    assert [action.step_id for action in repaired.plan.actions] == [1, 2]
    assert repaired.plan.actions[0].skill.value == "pick"
    assert repaired.plan.actions[1].skill.value == "place"
    repaired_validation = validator.validate(repaired.plan, state)
    assert repaired_validation.valid


class PlanGenerationForTest:
    """Minimal PlanGeneration stand-in to keep this test focused."""

    def __init__(self, plan: ModelPlan) -> None:
        self.plan = plan
        self.parse_error = None
        self.prompt = "test"
        self.prompt_id = "test"
        self.prompt_hash = "0" * 64
        self.response = VLMResponse(
            content="test", model="mock-vlm", latency_ms=1
        )


def test_collect_one_emits_shared_repairs_and_b0() -> None:
    validator, _ = _registry_and_state()

    def fake_response(payload: dict) -> str | dict:
        user_prompt = payload["messages"][1]["content"]
        if "Repair mode:" in user_prompt:
            return VALID_PLAN
        if "concise free text" in user_prompt:
            return (
                "1. pick red_cube_0 with right arm\n"
                "2. put red_cube_0 on tray_1 with right arm"
            )
        return {
            "actions": [
                {
                    "step_id": 1,
                    "skill": "pick",
                    "object_id": "blue_cube_0",
                    "target_id": None,
                    "arm": "right",
                }
            ]
        }

    client = MockVLMClient(fake_response)
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "collection.jsonl"
        summary = collect_one(
            SCENARIO,
            7,
            planner=InitialPlanner(client),
            direct_planner=DirectPlanner(client),
            repairer=PlanRepairer(client),
            validator=validator,
            logger=EpisodeLoggerForTest(output),
            repair_groups=("R0", "R1", "R2"),
        )
        assert summary["task_id"] == "test_task"
        assert summary["seed"] == 7
        assert summary["llm_calls_in_slice"] == 5  # P + R0/R1/R2 + B0
        lines = [json.loads(line) for line in output.read_text().splitlines()]
        assert [record["record_type"] for record in lines] == [
            "initial_shared_plan",
            "repair",
            "repair",
            "repair",
            "direct_b0_plan",
        ]
        assert all("raw_vlm_output" in record for record in lines)
        assert all(record["prompt_hash"] for record in lines)


class EpisodeLoggerForTest:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, record: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
