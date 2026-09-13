import json
from pathlib import Path

from ch3.capability.registry import CapabilityRegistry
from ch3.logger.episode_logger import EpisodeLogger
from ch3.metrics.repair_pressure_metrics import compute_pressure_metrics
from ch3.schema.model_plan import ModelPlan
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator
from ch3.vlm.mock import MockVLMClient
from ch3.vlm.planner import InitialPlanner
from ch3.vlm.prompts import PromptLibrary
from ch3.vlm.repair import PlanRepairer, RepairGeneration, required_transports

from scripts.repair_pressure import (
    ROUTED_GROUP,
    PRESSURE_TYPES,
    build_arg_parser,
    build_stress_plan,
    routed_repair,
    run_pressure_slice,
)


from tests.test_vlm_pipeline import SCENARIO, VALID_PLAN


def _validator() -> Validator:
    return Validator(
        scene_objects=set(SCENARIO["objects"]),
        registry=CapabilityRegistry.from_yaml("config/capability_registry.yaml"),
    )


def test_stress_plans_cover_deterministic_invalid_layers() -> None:
    validator = _validator()
    state = WorldState(
        objects=set(SCENARIO["objects"]),
        at=SCENARIO["initial_state"]["at"],
        holding=SCENARIO["initial_state"]["holding"],
    )
    valid_plan = ModelPlan.model_validate(VALID_PLAN)
    results = {}
    for pressure_type in PRESSURE_TYPES:
        stress_plan = build_stress_plan(valid_plan, SCENARIO, pressure_type)
        validation = validator.validate(stress_plan, state)
        results[pressure_type] = validation
        assert not validation.valid
        assert validation.error_code is not None

    assert results["duplicate_pick_after_prefix"].error_code.value == "ARM_NOT_EMPTY"
    assert results["unknown_object_after_prefix"].error_code.value == "UNKNOWN_OBJECT"
    assert results["invalid_target_after_prefix"].error_code.value == "UNKNOWN_OBJECT"
    assert results["place_before_pick"].error_code.value == "OBJECT_NOT_HELD"
    assert results["repeat_pick_after_valid_plan"].error_code.value == "STATE_TRANSITION_ERROR"


def test_pressure_runner_uses_frozen_repair_record(tmp_path: Path) -> None:
    client = MockVLMClient(VALID_PLAN)
    output = tmp_path / "records.jsonl"
    summary = run_pressure_slice(
        SCENARIO,
        7,
        planner=InitialPlanner(client, PromptLibrary()),
        repairer=PlanRepairer(client),
        validator=_validator(),
        logger=EpisodeLogger(output),
        pressure_types=("duplicate_pick_after_prefix",),
        repair_groups=("R0",),
    )
    assert summary["baseline_valid"] is True
    assert summary["repairs"]["duplicate_pick_after_prefix"]["R0"]["goal_satisfied"] is True

    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    record = records[0]
    assert record["record_type"] == "repair_pressure"
    assert record["pressure_type"] == "duplicate_pick_after_prefix"
    assert record["repair_mode"] == "R0"
    assert record["stress_valid"] is False
    assert record["valid"] is True
    assert record["goal_satisfied"] is True

    metrics = compute_pressure_metrics(records)
    assert metrics["R0"]["n"] == 1
    assert metrics["R0"]["CRR"] == 1.0
    assert metrics["R0"]["GSR_after_repair"] == 1.0
    assert metrics["R0"]["VGF"] == 0.0


def test_r2_accepts_empty_suffix_to_delete_illegal_tail() -> None:
    validator = _validator()
    state = WorldState(
        objects=set(SCENARIO["objects"]),
        at=SCENARIO["initial_state"]["at"],
        holding=SCENARIO["initial_state"]["holding"],
    )
    valid_plan = ModelPlan.model_validate(VALID_PLAN)
    source = InitialPlanner(MockVLMClient(VALID_PLAN), PromptLibrary()).plan(
        SCENARIO,
        seed=0,
    )
    stress_plan = build_stress_plan(
        valid_plan,
        SCENARIO,
        "repeat_pick_after_valid_plan",
    )
    stress_validation = validator.validate(stress_plan, state)
    assert stress_validation.validated_prefix == valid_plan.actions

    repaired = PlanRepairer(MockVLMClient({"actions": []})).repair(
        repair_mode="R2",
        task=SCENARIO,
        initial_generation=source,
        validation=stress_validation,
        initial_state=state,
        seed=0,
    )
    assert repaired.accepted is True
    assert repaired.reject_reason is None
    assert repaired.merged_with_prefix is True
    assert repaired.plan == valid_plan


def test_repair_prompt_does_not_leak_valid_source_plan(tmp_path: Path) -> None:
    client = MockVLMClient(VALID_PLAN)
    output = tmp_path / "records.jsonl"
    run_pressure_slice(
        SCENARIO,
        0,
        planner=InitialPlanner(client, PromptLibrary()),
        repairer=PlanRepairer(client),
        validator=_validator(),
        logger=EpisodeLogger(output),
        pressure_types=("unknown_object_after_prefix",),
        repair_groups=("R1",),
    )
    assert len(client.payloads) == 2
    repair_prompt = client.payloads[1]["messages"][1]["content"]
    assert "__unknown_object__" in repair_prompt


def test_routed_repair_accepts_state_aware_r2_without_fallback(tmp_path: Path) -> None:
    validator = _validator()
    state = WorldState(
        objects=set(SCENARIO["objects"]),
        at=SCENARIO["initial_state"]["at"],
        holding=SCENARIO["initial_state"]["holding"],
    )
    valid_plan = ModelPlan.model_validate(VALID_PLAN)
    source = InitialPlanner(MockVLMClient(VALID_PLAN), PromptLibrary()).plan(
        SCENARIO,
        seed=0,
    )
    stress_plan = build_stress_plan(
        valid_plan,
        SCENARIO,
        "unknown_object_after_prefix",
    )
    stress_validation = validator.validate(stress_plan, state)
    assert stress_validation.validated_prefix == valid_plan.actions[:1]

    def r2_success(payload):
        prompt = payload["messages"][1]["content"]
        if "Repair mode: R2" in prompt:
            return {"actions": [VALID_PLAN["actions"][1]]}
        return VALID_PLAN

    generation, route_info = routed_repair(
        repairer=PlanRepairer(MockVLMClient(r2_success), PromptLibrary()),
        task=SCENARIO,
        initial_generation=source,
        validation=stress_validation,
        initial_state=state,
        validator=validator,
        seed=0,
        temperature=0.0,
    )
    assert generation.accepted is True
    assert route_info == {
        "route_taken": ["R2"],
        "fallback_triggered": False,
        "r2_valid": True,
        "r2_goal_ok": True,
        "r2_pbw": False,
    }


def test_routed_repair_falls_back_to_r1_on_r2_failure(tmp_path: Path) -> None:
    validator = _validator()
    state = WorldState(
        objects=set(SCENARIO["objects"]),
        at=SCENARIO["initial_state"]["at"],
        holding=SCENARIO["initial_state"]["holding"],
    )
    valid_plan = ModelPlan.model_validate(VALID_PLAN)
    source = InitialPlanner(MockVLMClient(VALID_PLAN), PromptLibrary()).plan(
        SCENARIO,
        seed=0,
    )
    stress_plan = build_stress_plan(
        valid_plan,
        SCENARIO,
        "unknown_object_after_prefix",
    )
    stress_validation = validator.validate(stress_plan, state)

    def r2_failure_then_state_replan(payload):
        prompt = payload["messages"][1]["content"]
        if "Repair mode: R2" in prompt:
            return {"actions": []}
        return {"actions": [VALID_PLAN["actions"][1]]}

    output = tmp_path / "records.jsonl"
    summary = run_pressure_slice(
        SCENARIO,
        0,
        planner=InitialPlanner(MockVLMClient(VALID_PLAN), PromptLibrary()),
        repairer=PlanRepairer(
            MockVLMClient(r2_failure_then_state_replan), PromptLibrary()
        ),
        validator=validator,
        logger=EpisodeLogger(output),
        pressure_types=("unknown_object_after_prefix",),
        repair_groups=(ROUTED_GROUP,),
    )
    routed = summary["repairs"]["unknown_object_after_prefix"][ROUTED_GROUP]
    assert routed["fallback_triggered"] is True
    assert routed["route_taken"] == ["R2", "R1_FROM_STATE"]
    assert routed["fallback_mode"] == "R1_FROM_STATE"
    assert routed["r2_valid"] is False
    assert routed["valid"] is True
    assert routed["goal_satisfied"] is True

    records = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 1
    record = records[0]
    assert record["repair_mode"] == ROUTED_GROUP
    assert record["baseline"] == ROUTED_GROUP
    assert record["fallback_triggered"] is True
    assert record["r2_valid"] is False
    assert record["fallback_mode"] == "R1_FROM_STATE"

    metrics = compute_pressure_metrics(records)
    assert metrics[ROUTED_GROUP]["n"] == 1
    assert metrics[ROUTED_GROUP]["GSR_after_repair"] == 1.0
    assert metrics[ROUTED_GROUP]["VGF"] == 0.0


def test_routed_repair_uses_deterministic_truncation_when_goal_complete(
    tmp_path: Path,
) -> None:
    validator = _validator()
    state = WorldState(
        objects=set(SCENARIO["objects"]),
        at=SCENARIO["initial_state"]["at"],
        holding=SCENARIO["initial_state"]["holding"],
    )
    valid_plan = ModelPlan.model_validate(VALID_PLAN)
    source = InitialPlanner(MockVLMClient(VALID_PLAN), PromptLibrary()).plan(
        SCENARIO,
        seed=0,
    )
    stress_plan = build_stress_plan(
        valid_plan,
        SCENARIO,
        "repeat_pick_after_valid_plan",
    )
    stress_validation = validator.validate(stress_plan, state)
    assert stress_validation.validated_prefix == valid_plan.actions

    repaired, route_info = routed_repair(
        repairer=PlanRepairer(MockVLMClient({"actions": []})),
        task=SCENARIO,
        initial_generation=source,
        validation=stress_validation,
        initial_state=state,
        validator=validator,
        seed=0,
        temperature=0.3,
    )

    assert route_info["route_taken"] == ["DETERMINISTIC_TRUNCATION"]
    assert route_info["fallback_triggered"] is False
    assert route_info["r2_skipped"] is True
    assert route_info["deterministic_truncation"] is True
    assert repaired.accepted is True
    assert repaired.plan == valid_plan
    assert repaired.merged_with_prefix is True

    output = tmp_path / "routed_truncation.jsonl"
    summary = run_pressure_slice(
        SCENARIO,
        0,
        planner=InitialPlanner(MockVLMClient(VALID_PLAN), PromptLibrary()),
        repairer=PlanRepairer(MockVLMClient({"actions": []})),
        validator=validator,
        logger=EpisodeLogger(output),
        pressure_types=("repeat_pick_after_valid_plan",),
        repair_groups=(ROUTED_GROUP,),
    )
    records = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert summary["pressure_calls"] == 1
    assert records[0]["route_taken"] == ["DETERMINISTIC_TRUNCATION"]
    assert records[0]["valid"] is True
    assert records[0]["goal_satisfied"] is True


def test_pressure_runner_rejects_state_valid_goal_failing_source(
    tmp_path: Path,
) -> None:
    """A state-valid partial solution is not a valid pressure source."""
    validator = _validator()
    state = WorldState(
        objects=set(SCENARIO["objects"]),
        at=SCENARIO["initial_state"]["at"],
        holding=SCENARIO["initial_state"]["holding"],
    )
    partial_plan = ModelPlan.model_validate(
        {
            "actions": [
                VALID_PLAN["actions"][0],
                {
                    **VALID_PLAN["actions"][1],
                    "target_id": "table",
                },
            ]
        }
    )
    output = tmp_path / "source_goal.jsonl"

    summary = run_pressure_slice(
        SCENARIO,
        0,
        planner=InitialPlanner(MockVLMClient(VALID_PLAN), PromptLibrary()),
        repairer=PlanRepairer(MockVLMClient(VALID_PLAN)),
        validator=validator,
        logger=EpisodeLogger(output),
        pressure_types=("repeat_pick_after_valid_plan",),
        repair_groups=(ROUTED_GROUP,),
        source_plan=partial_plan,
    )

    assert summary["baseline_valid"] is False
    assert summary["pressure_calls"] == 0
    assert summary["repairs"] == {}

    records = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(records) == 1
    record = records[0]
    assert record["record_type"] == "pressure_source"
    assert record["valid"] is True
    assert record["goal_satisfied"] is False
    assert record["pressure_source_valid"] is True
    assert record["pressure_source_goal_satisfied"] is False


def test_r1_from_state_prompt_allows_empty_suffix_when_goal_complete() -> None:
    validator = _validator()
    state = WorldState(
        objects=set(SCENARIO["objects"]),
        at=SCENARIO["initial_state"]["at"],
        holding=SCENARIO["initial_state"]["holding"],
    )
    valid_plan = ModelPlan.model_validate(VALID_PLAN)
    source = InitialPlanner(MockVLMClient(VALID_PLAN), PromptLibrary()).plan(
        SCENARIO,
        seed=0,
    )
    stress_plan = build_stress_plan(
        valid_plan,
        SCENARIO,
        "repeat_pick_after_valid_plan",
    )
    stress_validation = validator.validate(stress_plan, state)

    repaired = PlanRepairer(MockVLMClient({"actions": []})).repair(
        repair_mode="R1_FROM_STATE",
        task=SCENARIO,
        initial_generation=source,
        validation=stress_validation,
        initial_state=state,
        seed=0,
    )
    assert repaired.accepted is True
    assert repaired.plan == valid_plan
    assert "if `remaining_goal_facts` is empty" in repaired.prompt


def test_r1_from_state_replans_suffix_from_prefix_final_state() -> None:
    validator = _validator()
    state = WorldState(
        objects=set(SCENARIO["objects"]),
        at=SCENARIO["initial_state"]["at"],
        holding=SCENARIO["initial_state"]["holding"],
    )
    valid_plan = ModelPlan.model_validate(VALID_PLAN)
    source = InitialPlanner(MockVLMClient(VALID_PLAN), PromptLibrary()).plan(
        SCENARIO,
        seed=0,
    )
    stress_plan = build_stress_plan(
        valid_plan,
        SCENARIO,
        "unknown_object_after_prefix",
    )
    stress_validation = validator.validate(stress_plan, state)
    assert stress_validation.final_state is not None

    suffix = {"actions": [VALID_PLAN["actions"][1]]}
    repaired: RepairGeneration = PlanRepairer(
        MockVLMClient(suffix), PromptLibrary()
    ).repair(
        repair_mode="R1_FROM_STATE",
        task=SCENARIO,
        initial_generation=source,
        validation=stress_validation,
        initial_state=state,
        seed=0,
    )
    assert repaired.accepted is True
    assert repaired.repair_mode == "R1_FROM_STATE"
    assert repaired.merged_with_prefix is True
    assert repaired.plan == valid_plan

    prompt = repaired.prompt
    assert "Repair mode: R1_FROM_STATE" in prompt
    assert "holding(right, red_cube_0)" in prompt
    assert "executed_prefix" in prompt
    assert "satisfy every fact in" in prompt
    assert "remaining_goal_facts" in prompt
    assert (
        "Returning only the immediate place for an object already held by "
        "the prefix is insufficient" in prompt
    )
    assert '"original_plan":' not in prompt
    assert '"required_transports"' in prompt
    assert '"object_id": "red_cube_0"' in prompt


def test_required_transports_are_derived_from_goal_and_prefix_state() -> None:
    task = {
        "goal": {
            "facts": [
                "on(red_cube_0, tray_1)",
                "on(blue_cube_2, table)",
                "not_a_transport(green_cube_3)",
            ]
        }
    }
    state = WorldState(
        objects={"red_cube_0", "tray_1", "blue_cube_2", "green_cube_3"},
        at={"red_cube_0": "table", "tray_1": "table"},
        holding={"left": "blue_cube_2"},
    )
    assert required_transports(task, state) == [
        {
            "kind": "transport",
            "skill": "place",
            "object_id": "blue_cube_2",
            "target_id": "table",
            "currently_held": True,
            "current_location": None,
        },
        {
            "kind": "transport",
            "skill": "place",
            "object_id": "red_cube_0",
            "target_id": "tray_1",
            "currently_held": False,
            "current_location": "table",
        },
    ]


def test_cli_accepts_routed_repair_group() -> None:
    args = build_arg_parser().parse_args(
        [
            "--output",
            "data/collections/tmp.jsonl",
            "--repair-groups",
            ROUTED_GROUP,
        ]
    )
    assert args.repair_groups == [ROUTED_GROUP]


def test_main_sets_scene_objects_per_task(tmp_path, monkeypatch, capsys) -> None:
    import scripts.repair_pressure as runner

    seen_validators = []

    def fake_run_pressure_slice(task, seed, **kwargs):
        seen_validators.append(set(kwargs["validator"].scene_objects))
        return {
            "task_id": task["task_id"],
            "seed": seed,
            "baseline_valid": True,
            "pressure_calls": 0,
            "repairs": {},
        }

    monkeypatch.setattr(runner, "run_pressure_slice", fake_run_pressure_slice)
    output = tmp_path / "pressure.jsonl"
    rc = runner.main(
        [
            "--scenarios",
            "data/scenarios/stress_tasks_v8_pilot_v2.jsonl",
            "--output",
            str(output),
            "--client",
            "mock",
            "--limit",
            "2",
        ]
    )
    assert rc == 0
    assert seen_validators == [
        {"red_cube_0", "red_wedge_1", "yellow_block_2", "yellow_wedge_3",
         "green_box_4", "white_tray_5", "white_wedge_6", "pink_cube_7",
         "brown_sphere_8", "cyan_cube_9"},
        {"cyan_cube_10", "cyan_block_11", "purple_sphere_12", "purple_cube_13",
         "brown_tray_14", "white_box_15", "gray_block_16", "black_wedge_17",
         "black_cube_18", "brown_sphere_19"},
    ]


def test_load_frozen_sources_reuses_baseline_plans(tmp_path):
    """Frozen baseline reuse: sources load by (task_id, seed), skipping planner."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from repair_pressure import load_frozen_sources  # noqa: E402

    plan = ModelPlan(
        actions=[
            {
                "step_id": 1,
                "skill": "pick",
                "object_id": "red_cube_0",
                "target_id": None,
                "arm": "left",
            }
        ]
    )
    record = {
        "task_id": "t1",
        "seed": 0,
        "record_type": "repair_pressure",
        "pressure_source_plan": plan.model_dump(),
    }
    path = tmp_path / "frozen.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    sources = load_frozen_sources(path)
    assert ("t1", 0) in sources
    assert sources[("t1", 0)].actions[0].object_id == "red_cube_0"
