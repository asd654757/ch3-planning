import json

from ch3.schema.model_plan import ModelPlan
from ch3.state.world_state import WorldState
from ch3.validator.result import ValidationResult
from ch3.vlm.client import VLMResponse
from ch3.vlm.planner import PlanGeneration
from ch3.vlm.repair import PlanRepairer


class CaptureClient:
    def __init__(self) -> None:
        self.user_prompts: list[str] = []
        self.image_paths: list[str | None] = []

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_path: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        seed: int | None = None,
        json_mode: bool | None = None,
    ) -> VLMResponse:
        self.user_prompts.append(user_prompt)
        self.image_paths.append(image_path)
        return VLMResponse(
            content='{"actions":[]}',
            model="mock",
            latency_ms=0,
            finish_reason="stop",
        )


def test_sparse_state_prompt_omits_post_execution_state() -> None:
    task = {
        "instruction": "Use the right arm to push the red cube to the goal pad.",
        "objects": ["red_cube", "goal_pad"],
        "goal": {"facts": ["pushed_to(red_cube, goal_pad)"]},
        "state_visibility": "sparse",
    }
    plan = ModelPlan.model_validate(
        {
            "actions": [
                {
                    "step_id": 1,
                    "skill": "push",
                    "object_id": "red_cube",
                    "target_id": "goal_pad",
                    "arm": "right",
                }
            ]
        }
    )
    generation = PlanGeneration(
        plan=plan,
        response=VLMResponse(content="mock", model="mock", latency_ms=0),
    )
    state = WorldState.table_scene(task["objects"])
    validation = ValidationResult(
        valid=False,
        first_invalid_step=1,
        error_code=None,
        message="metaworld_execution_failure",
        layer="execution",
        validated_prefix=[],
        final_state=state,
    )
    client = CaptureClient()
    repairer = PlanRepairer(client, max_tokens=64, json_mode=True)

    repairer.repair(
        repair_mode="R1_FROM_STATE",
        task=task,
        initial_generation=generation,
        validation=validation,
        initial_state=state,
    )

    prompt = client.user_prompts[0]
    assert '"current_state"' not in prompt
    assert '"prefix_final_state"' not in prompt
    assert '"held_objects"' not in prompt
    assert '"required_transports"' not in prompt
    assert '"remaining_goal_facts"' in prompt
    assert "No structured post-execution state" in prompt


def test_bounded_visual_protocol_allows_observation_but_preserves_closed_world() -> None:
    task = {
        "instruction": "Use the right arm to push the red cube to the goal pad.",
        "objects": ["red_cube", "goal_pad"],
        "goal": {"facts": ["pushed_to(red_cube, goal_pad)"]},
        "state_visibility": "sparse",
        "visual_feedback": True,
        "visual_feedback_protocol": "bounded_execution_observation_v1",
        "image_path": "/tmp/frozen-failure-frame.png",
    }
    plan = ModelPlan.model_validate({"actions": [{"step_id": 1, "skill": "push", "object_id": "red_cube", "target_id": "goal_pad", "arm": "right"}]})
    state = WorldState.table_scene(task["objects"])
    validation = ValidationResult(valid=False, first_invalid_step=1, error_code=None, message="metaworld_execution_failure", layer="execution", validated_prefix=[], final_state=state)
    client = CaptureClient()
    repairer = PlanRepairer(client, max_tokens=64, json_mode=True)
    repairer.repair(repair_mode="R1_FROM_STATE", task=task, initial_generation=PlanGeneration(plan=plan, response=VLMResponse(content="mock", model="mock", latency_ms=0)), validation=validation, initial_state=state)
    prompt = client.user_prompts[0]
    assert "bounded execution observation" in prompt
    assert "Do not invent object IDs" in prompt
    assert "current_state" not in prompt
    assert client.image_paths == ["/tmp/frozen-failure-frame.png"]
