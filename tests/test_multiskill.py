from ch3.capability.registry import CapabilityRegistry
from ch3.schema.model_plan import Arm, ModelPlan, ModelPlanAction, Skill
from ch3.state.world_state import WorldState
from ch3.tasks.generator import TaskGenerator
from ch3.validator.pipeline import Validator
from ch3.vlm.closure import closed_world_infeasibility
from ch3.vlm.repair import required_transports

from scripts.repair_pressure import build_stress_plan


def _tasks() -> dict[str, dict]:
    rules = {
        "multiskill": {
            "count_per_family": 1,
            "families": ["pick_place", "push", "press"],
            "colors": ["red", "blue", "green"],
            "shapes": ["cube", "block"],
            "containers": ["tray"],
        }
    }
    return {task["task_family"]: task for task in TaskGenerator(rules).generate_all()}


def _plan(task: dict, actions: list[ModelPlanAction]) -> ModelPlan:
    return ModelPlan(
        actions=[
            action.model_copy(update={"step_id": index})
            for index, action in enumerate(actions, 1)
        ]
    )


def _validator(task: dict) -> Validator:
    return Validator(
        scene_objects=set(task["objects"]),
        registry=CapabilityRegistry.from_yaml("config/capability_registry.yaml"),
    )


def _state(task: dict) -> WorldState:
    return WorldState(
        objects=set(task["objects"]),
        at=task["initial_state"]["at"],
        holding=task["initial_state"]["holding"],
    )


def test_multiskill_tasks_are_generated_and_solvable() -> None:
    tasks = _tasks()
    assert set(tasks) == {"pick_place", "push", "press"}
    for task in tasks.values():
        assert task["solvable"] is True
        assert task["hard_factors"]["skill_family"] in tasks

    assert tasks["pick_place"]["goal"]["facts"][0].startswith("on(")
    assert tasks["push"]["goal"]["facts"][0].startswith("pushed_to(")
    assert tasks["press"]["goal"]["facts"][0].startswith("pressed(")


def test_multiskill_valid_plans_pass_the_validator() -> None:
    tasks = _tasks()
    pick = tasks["pick_place"]
    obj, target = [part.strip() for part in pick["goal"]["facts"][0][3:-1].split(",")]
    push = tasks["push"]
    pushed_obj, pushed_target = [
        part.strip()
        for part in push["goal"]["facts"][0][len("pushed_to("):-1].split(",")
    ]
    pressed_obj = tasks["press"]["goal"]["facts"][0][len("pressed("):-1]

    plans = {
        "pick_place": _plan(
            pick,
            [
                ModelPlanAction(step_id=1, skill=Skill.PICK, object_id=obj, arm=Arm.RIGHT),
                ModelPlanAction(
                    step_id=2,
                    skill=Skill.PLACE,
                    object_id=obj,
                    target_id=target,
                    arm=Arm.RIGHT,
                ),
            ],
        ),
        "push": _plan(
            push,
            [
                ModelPlanAction(
                    step_id=1,
                    skill=Skill.PUSH,
                    object_id=pushed_obj,
                    target_id=pushed_target,
                    arm=Arm.RIGHT,
                )
            ],
        ),
        "press": _plan(
            tasks["press"],
            [
                ModelPlanAction(
                    step_id=1, skill=Skill.PRESS, object_id=pressed_obj, arm=Arm.LEFT
                )
            ],
        ),
    }
    for family, plan in plans.items():
        validation = _validator(tasks[family]).validate(plan, _state(tasks[family]))
        assert validation.valid, validation.message


def test_generic_pressure_types_are_invalid_for_each_skill_family() -> None:
    tasks = _tasks()
    obj = tasks["push"]["objects"][0]
    button = tasks["press"]["objects"][0]
    pick_obj, target = [
        part.strip()
        for part in tasks["pick_place"]["goal"]["facts"][0][3:-1].split(",")
    ]

    plans = {
        "pick_place": ModelPlan(
            actions=[
                ModelPlanAction(
                    step_id=1, skill=Skill.PICK, object_id=pick_obj, arm=Arm.RIGHT
                ),
                ModelPlanAction(
                    step_id=2,
                    skill=Skill.PLACE,
                    object_id=pick_obj,
                    target_id=target,
                    arm=Arm.RIGHT,
                ),
            ]
        ),
        "push": ModelPlan(
            actions=[
                ModelPlanAction(
                    step_id=1,
                    skill=Skill.PUSH,
                    object_id=obj,
                    target_id="goal_pad",
                    arm=Arm.RIGHT,
                )
            ]
        ),
        "press": ModelPlan(
            actions=[
                ModelPlanAction(
                    step_id=1, skill=Skill.PRESS, object_id=button, arm=Arm.LEFT
                )
            ]
        ),
    }
    expected_layers = {
        "duplicate_skill_after_prefix": {"pick_place": "state", "push": "state", "press": "state"},
        "unknown_object_after_generic_prefix": {
            "pick_place": "object",
            "push": "object",
            "press": "object",
        },
        "invalid_action_after_prefix": {
            "pick_place": "object",
            "push": "object",
            "press": "state",
        },
    }
    for family, plan in plans.items():
        for pressure, expected_layer in expected_layers.items():
            stress_plan = build_stress_plan(plan, tasks[family], pressure)
            validation = _validator(tasks[family]).validate(
                stress_plan, _state(tasks[family])
            )
            assert not validation.valid
            assert validation.layer == expected_layers[pressure][family]


def test_multiskill_ghost_goal_facts_are_rejected_by_closure() -> None:
    tasks = _tasks()
    assert closed_world_infeasibility(
        tasks["pick_place"] | {"objects": [tasks["pick_place"]["objects"][0]]}
    ) is not None
    assert closed_world_infeasibility(
        tasks["push"] | {"objects": [tasks["push"]["objects"][0]]}
    ) is not None
    assert closed_world_infeasibility(
        tasks["press"] | {"objects": ["not_the_button"]}
    ) is not None


def test_required_transports_support_push_and_press() -> None:
    state = WorldState(
        objects={"cube_0", "goal_pad", "button_0"},
        at={"cube_0": "table", "goal_pad": "table"},
    )
    task = {"goal": {"facts": ["pushed_to(cube_0, goal_pad)", "pressed(button_0)"]}}
    assert required_transports(task, state) == [
        {
            "kind": "press",
            "skill": "press",
            "object_id": "button_0",
        },
        {
            "kind": "transport",
            "skill": "push",
            "object_id": "cube_0",
            "target_id": "goal_pad",
            "currently_held": False,
            "current_location": "table",
        },
    ]
