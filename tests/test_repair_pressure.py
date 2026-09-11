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
from ch3.vlm.repair import PlanRepairer

from scripts.repair_pressure import (
    PRESSURE_TYPES,
    build_stress_plan,
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

    records = [json.loads(line) for line in output.read_text().splitlines()]
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
    assert metrics["R0"]["FRR"] == 0.0


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
