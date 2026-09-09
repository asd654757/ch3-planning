import pytest

from ch3.capability.registry import CapabilityRegistry
from ch3.state.world_state import WorldState
from ch3.schema.model_plan import Arm, ModelPlan, ModelPlanAction, Skill


SCENE = {"red_cube_0", "blue_cube_0", "tray_0", "box_0"}
REGISTRY_PATH = "config/capability_registry.yaml"


@pytest.fixture()
def registry() -> CapabilityRegistry:
    return CapabilityRegistry.from_yaml(REGISTRY_PATH)


@pytest.fixture()
def scene_objects() -> set[str]:
    return set(SCENE)


@pytest.fixture()
def init_state() -> WorldState:
    return WorldState.table_scene(SCENE)


def act(step_id: int, skill: Skill, object_id: str, arm: Arm, target_id=None) -> ModelPlanAction:
    return ModelPlanAction(step_id=step_id, skill=skill, object_id=object_id, target_id=target_id, arm=arm)


def plan(*actions: ModelPlanAction) -> ModelPlan:
    return ModelPlan(actions=list(actions))
