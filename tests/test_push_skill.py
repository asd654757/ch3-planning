from ch3.capability.registry import CapabilityRegistry
from ch3.compiler.executable_plan import compile_plan
from ch3.errors import ErrorCode
from ch3.goal.goal_checker import goal_satisfied
from ch3.schema.model_plan import Arm, GoalSpec, ModelPlan, ModelPlanAction, Skill
from ch3.state.simulator import step
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator


def push_action(step_id: int = 1) -> ModelPlanAction:
    return ModelPlanAction(
        step_id=step_id,
        skill=Skill.PUSH,
        object_id="red_cube_0",
        target_id="goal_pad",
        arm=Arm.RIGHT,
    )


def test_push_is_registered_and_compiles(registry: CapabilityRegistry):
    assert registry.has_skill(Skill.PUSH)
    assert registry.policy_for(Skill.PUSH) == "metaworld_push"
    assert registry.primitive_for(Skill.PUSH) == "push"

    executable = compile_plan(ModelPlan(actions=[push_action()]), registry)
    assert executable.steps[0].policy_id == "metaworld_push"
    assert executable.steps[0].primitive == "push"
    assert executable.steps[0].args["target_id"] == "goal_pad"


def test_push_validates_and_satisfies_goal(
    registry: CapabilityRegistry,
    scene_objects,
    init_state: WorldState,
):
    validator = Validator(
        scene_objects=scene_objects,
        registry=registry,
        special_targets={"table", "goal_pad"},
    )
    result = validator.validate(ModelPlan(actions=[push_action()]), init_state)
    assert result.valid, result.message
    assert "pushed_to(red_cube_0, goal_pad)" in result.final_state.facts()
    assert goal_satisfied(
        result.final_state,
        GoalSpec(facts=["pushed_to(red_cube_0, goal_pad)"]),
        arms={"left", "right"},
    )


def test_push_rejects_unknown_target(
    registry: CapabilityRegistry,
    scene_objects,
    init_state: WorldState,
):
    action = ModelPlanAction(
        step_id=1,
        skill=Skill.PUSH,
        object_id="red_cube_0",
        target_id="ghost_pad",
        arm=Arm.RIGHT,
    )
    validator = Validator(
        scene_objects=scene_objects,
        registry=registry,
        special_targets={"table", "goal_pad"},
    )
    result = validator.validate(ModelPlan(actions=[action]), init_state)
    assert not result.valid
    assert result.error_code == ErrorCode.UNKNOWN_OBJECT
    assert result.layer == "object"


def test_push_rejects_while_holding(registry: CapabilityRegistry):
    state = WorldState.table_scene(["red_cube_0", "blue_cube_0"])
    state.holding["right"] = "blue_cube_0"
    _next_state, ok, code, message = step(state, push_action())
    assert not ok
    assert code == ErrorCode.ARM_NOT_EMPTY
    assert "right" in message


def press_action(step_id: int = 1) -> ModelPlanAction:
    return ModelPlanAction(
        step_id=step_id,
        skill=Skill.PRESS,
        object_id="button_0",
        arm=Arm.RIGHT,
    )


def test_press_is_registered_and_compiles(registry: CapabilityRegistry):
    assert registry.has_skill(Skill.PRESS)
    assert registry.policy_for(Skill.PRESS) == "metaworld_button_press"
    assert registry.primitive_for(Skill.PRESS) == "press"

    executable = compile_plan(ModelPlan(actions=[press_action()]), registry)
    assert executable.steps[0].policy_id == "metaworld_button_press"
    assert executable.steps[0].primitive == "press"


def test_press_validates_and_satisfies_goal(
    registry: CapabilityRegistry,
    init_state: WorldState,
):
    scene_objects = {"button_0"}
    state = WorldState.table_scene(scene_objects)
    validator = Validator(
        scene_objects=scene_objects,
        registry=registry,
        special_targets={"table"},
    )
    result = validator.validate(ModelPlan(actions=[press_action()]), state)
    assert result.valid, result.message
    assert "pressed(button_0)" in result.final_state.facts()
    assert goal_satisfied(
        result.final_state,
        GoalSpec(facts=["pressed(button_0)"]),
        arms={"left", "right"},
    )


def test_press_rejects_target_parameter(
    registry: CapabilityRegistry,
):
    action = ModelPlanAction(
        step_id=1,
        skill=Skill.PRESS,
        object_id="button_0",
        target_id="ghost_pad",
        arm=Arm.RIGHT,
    )
    validator = Validator(
        scene_objects={"button_0"},
        registry=registry,
        special_targets={"table"},
    )
    result = validator.validate(ModelPlan(actions=[action]), WorldState.table_scene(["button_0"]))
    assert not result.valid
    assert result.error_code == ErrorCode.MISSING_PARAMETER
    assert result.layer == "capability"


def test_press_rejects_while_holding(registry: CapabilityRegistry):
    state = WorldState.table_scene(["button_0", "red_cube_0"])
    state.holding["right"] = "red_cube_0"
    _next_state, ok, code, message = step(state, press_action())
    assert not ok
    assert code == ErrorCode.ARM_NOT_EMPTY
