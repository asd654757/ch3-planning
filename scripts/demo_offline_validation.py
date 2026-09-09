"""离线演示：四层校验 + 首错定位 + ExecutablePlan 编译（不接 VLM/真机）。

用法：./.venv/bin/python scripts/demo_offline_validation.py
"""

from __future__ import annotations

from ch3.capability.registry import CapabilityRegistry
from ch3.compiler import compile_plan
from ch3.schema.model_plan import Arm, ModelPlan, ModelPlanAction, Skill
from ch3.state.world_state import WorldState
from ch3.validator import ERROR_ID, Validator

SCENE = {"red_cube_0", "blue_cube_0", "tray_0", "box_0"}


def act(step_id: int, skill: Skill, object_id: str, arm: Arm, target_id=None) -> ModelPlanAction:
    return ModelPlanAction(step_id=step_id, skill=skill, object_id=object_id, target_id=target_id, arm=arm)


def main() -> None:
    registry = CapabilityRegistry.from_yaml("config/capability_registry.yaml")
    validator = Validator(scene_objects=set(SCENE), registry=registry)

    plans: list[tuple[str, ModelPlan]] = [
        ("合法 3 步", ModelPlan(actions=[
            act(1, Skill.PICK, "red_cube_0", Arm.LEFT),
            act(2, Skill.PLACE, "red_cube_0", Arm.LEFT, "tray_0"),
            act(3, Skill.PICK, "blue_cube_0", Arm.RIGHT),
        ])),
        ("未知对象", ModelPlan(actions=[act(1, Skill.PICK, "ghost_0", Arm.LEFT)])),
        ("arm 占用", ModelPlan(actions=[
            act(1, Skill.PICK, "red_cube_0", Arm.LEFT),
            act(2, Skill.PICK, "blue_cube_0", Arm.LEFT),
        ])),
        ("place-before-pick", ModelPlan(actions=[
            act(1, Skill.PLACE, "red_cube_0", Arm.LEFT, "tray_0"),
        ])),
    ]

    for name, p in plans:
        r = validator.validate(p, WorldState.table_scene(SCENE))
        if r.valid:
            print(f"[{name}] VALID  前缀步数={len(r.validated_prefix)}")
        else:
            print(f"[{name}] INVALID 步={r.first_invalid_step} 层={r.layer} "
                  f"错误={ERROR_ID.get(r.error_code, '?')} {r.error_code.value} — {r.message}")

    # 编译演示：合法计划 -> ExecutablePlan（补 policy_id / primitive）
    ok_plan = plans[0][1]
    exe = compile_plan(ok_plan, registry)
    print("\nExecutablePlan:")
    for s in exe.to_list():
        print(" ", s)


if __name__ == "__main__":
    main()
