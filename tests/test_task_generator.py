"""Tests for the formal task generator (stage 3, priority 2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from ch3.vlm.planner import DirectPlanner, InitialPlanner
from ch3.vlm.prompts import PromptLibrary
from ch3.vlm.mock import MockVLMClient
from ch3.tasks.generator import TaskGenerator, build_arg_parser, main


RULES_PATH = Path(__file__).parent.parent / "config" / "task_rules.yaml"
RULES_V6_PATH = Path(__file__).parent.parent / "config" / "task_rules_v6.yaml"
RULES_V8_PILOT_PATH = Path(__file__).parent.parent / "config" / "task_rules_v8_pilot.yaml"


@pytest.fixture()
def rules() -> dict:
    with open(RULES_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestTaskGenerator:
    def test_generates_40_tasks(self, rules: dict) -> None:
        gen = TaskGenerator(rules, seed=42)
        tasks = gen.generate_all()
        assert len(tasks) == 55

    def test_difficulty_distribution(self, rules: dict) -> None:
        gen = TaskGenerator(rules, seed=42)
        tasks = gen.generate_all()
        counts = {"easy": 0, "medium": 0, "hard": 0, "infeasible": 0}
        for t in tasks:
            counts[t["difficulty"]] += 1
        assert counts == {"easy": 10, "medium": 5, "hard": 35, "infeasible": 5}

    def test_all_feasible_tasks_solvable(self, rules: dict) -> None:
        gen = TaskGenerator(rules, seed=42)
        tasks = gen.generate_all()
        for t in tasks:
            if t["difficulty"] != "infeasible":
                assert t["solvable"], f"{t['task_id']} should be solvable"

    def test_all_infeasible_tasks_unsolvable(self, rules: dict) -> None:
        gen = TaskGenerator(rules, seed=42)
        tasks = gen.generate_all()
        for t in tasks:
            if t["difficulty"] == "infeasible":
                assert not t["solvable"], f"{t['task_id']} should be infeasible"

    def test_hard_tasks_have_at_least_2_factors(self, rules: dict) -> None:
        gen = TaskGenerator(rules, seed=42)
        tasks = gen.generate_all()
        for t in tasks:
            if t["difficulty"] == "hard":
                active = sum(1 for v in t.get("hard_factors", {}).values() if v)
                assert active >= 2, f"{t['task_id']} only has {active} hard factors"

    def test_easy_tasks_no_distractors(self, rules: dict) -> None:
        gen = TaskGenerator(rules, seed=42)
        tasks = gen.generate_all()
        for t in tasks:
            if t["difficulty"] == "easy":
                assert len(t["objects"]) == 2, f"{t['task_id']} should have exactly 2 objects"

    def test_unique_task_ids(self, rules: dict) -> None:
        gen = TaskGenerator(rules, seed=42)
        tasks = gen.generate_all()
        ids = [t["task_id"] for t in tasks]
        assert len(ids) == len(set(ids))

    def test_unique_object_names(self, rules: dict) -> None:
        gen = TaskGenerator(rules, seed=42)
        tasks = gen.generate_all()
        all_names = []
        for t in tasks:
            all_names.extend(t["objects"])
        assert len(all_names) == len(set(all_names)), "Object names must be globally unique"

    def test_v6_scene_object_descriptions_are_unique(self) -> None:
        with open(RULES_V6_PATH, "r", encoding="utf-8") as f:
            rules = yaml.safe_load(f)
        tasks = TaskGenerator(rules, seed=42).generate_all()
        for task in tasks:
            descriptions = [
                tuple(name.rsplit("_", 2)[:2])
                for name in task["objects"]
            ]
            assert len(descriptions) == len(set(descriptions)), (
                f"{task['task_id']} has ambiguous color/shape descriptions: "
                f"{sorted(descriptions)}"
            )

    def test_required_fields(self, rules: dict) -> None:
        gen = TaskGenerator(rules, seed=42)
        tasks = gen.generate_all()
        required = {"task_id", "difficulty", "instruction", "objects", "initial_state", "goal"}
        for t in tasks:
            missing = required - set(t.keys())
            assert not missing, f"{t['task_id']} missing: {missing}"

    def test_deterministic_with_same_seed(self, rules: dict) -> None:
        t1 = TaskGenerator(rules, seed=42).generate_all()
        t2 = TaskGenerator(rules, seed=42).generate_all()
        assert [t["task_id"] for t in t1] == [t["task_id"] for t in t2]

    def test_different_seed_different_tasks(self, rules: dict) -> None:
        t1 = TaskGenerator(rules, seed=42).generate_all()
        t2 = TaskGenerator(rules, seed=99).generate_all()
        ids1 = {t["task_id"] for t in t1}
        ids2 = {t["task_id"] for t in t2}
        # IDs have same pattern but object names should differ
        objs1 = {o for t in t1 for o in t["objects"]}
        objs2 = {o for t in t2 for o in t["objects"]}
        assert objs1 != objs2


class TestV8StressGenerator:
    def test_v8_pilot_generates_expected_families(self) -> None:
        with open(RULES_V8_PILOT_PATH, "r", encoding="utf-8") as f:
            rules = yaml.safe_load(f)
        tasks = TaskGenerator(rules, seed=20260911).generate_all()
        counts: dict[str, int] = {}
        for task in tasks:
            counts[task["difficulty"]] = counts.get(task["difficulty"], 0) + 1
        assert counts == {
            "attribute_grouped": 8,
            "exclusion_constraint": 8,
            "logical_conflict": 6,
        }

    def test_v8_scene_object_descriptions_are_unique(self) -> None:
        with open(RULES_V8_PILOT_PATH, "r", encoding="utf-8") as f:
            rules = yaml.safe_load(f)
        tasks = TaskGenerator(rules, seed=20260911).generate_all()
        for task in tasks:
            descriptions = [tuple(name.rsplit("_", 2)[:2]) for name in task["objects"]]
            assert len(descriptions) == len(set(descriptions)), task["task_id"]

    def test_attribute_grouped_tasks_share_color_and_target(self) -> None:
        with open(RULES_V8_PILOT_PATH, "r", encoding="utf-8") as f:
            rules = yaml.safe_load(f)
        tasks = TaskGenerator(rules, seed=20260911).generate_all()
        for task in tasks:
            if task["difficulty"] != "attribute_grouped":
                continue
            assert task["solvable"]
            parsed = [fact[3:-1].split(", ") for fact in task["goal"]["facts"]]
            assert len(parsed) >= 2
            objects = [obj for obj, _ in parsed]
            by_color: dict[str, set[str]] = {}
            for name, target in parsed:
                by_color.setdefault(name.rsplit("_", 2)[0], set()).add(target)
            assert len(by_color) >= 1
            for targets in by_color.values():
                assert len(targets) == 1
            for color in by_color:
                assert f"every {color} object" in task["instruction"]

    def test_exclusion_constraints_are_goal_measurable(self) -> None:
        with open(RULES_V8_PILOT_PATH, "r", encoding="utf-8") as f:
            rules = yaml.safe_load(f)
        tasks = TaskGenerator(rules, seed=20260911).generate_all()
        for task in tasks:
            if task["difficulty"] != "exclusion_constraint":
                continue
            assert task["solvable"]
            assert "do not move any" in task["instruction"].lower()
            assert task["hard_factors"]["generic_constraint"]
            table_facts = [fact for fact in task["goal"]["facts"] if fact.endswith(", table)")]
            assert table_facts

    def test_logical_conflict_tasks_are_infeasible(self) -> None:
        with open(RULES_V8_PILOT_PATH, "r", encoding="utf-8") as f:
            rules = yaml.safe_load(f)
        tasks = TaskGenerator(rules, seed=20260911).generate_all()
        conflicts = [t for t in tasks if t["difficulty"] == "logical_conflict"]
        assert conflicts
        for task in conflicts:
            assert not task["solvable"]
            obj0, target0 = task["goal"]["facts"][0][3:-1].split(", ")
            obj1, target1 = task["goal"]["facts"][1][3:-1].split(", ")
            assert obj0 == obj1
            assert target0 != target1
            assert target0 != "table" and target1 != "table"

    def test_planners_detect_logical_conflict_deterministically(self) -> None:
        scenario = {
            "task_id": "logical_conflict_test",
            "difficulty": "logical_conflict",
            "instruction": "Put the red cube into both containers.",
            "objects": ["red_cube_0", "tray_1", "box_2"],
            "initial_state": {
                "at": {"red_cube_0": "table", "tray_1": "table", "box_2": "table"},
                "holding": {},
            },
            "goal": {"facts": ["on(red_cube_0, tray_1)", "on(red_cube_0, box_2)"]},
            "image_path": None,
        }
        valid_but_insufficient_plan = {
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
        initial = InitialPlanner(
            MockVLMClient(valid_but_insufficient_plan), PromptLibrary()
        ).plan(scenario, seed=1)
        direct = DirectPlanner(
            MockVLMClient("1. pick red_cube_0\n2. place red_cube_0 on tray_1"),
            PromptLibrary(),
        ).plan(scenario, seed=1)
        assert initial.infeasible and direct.infeasible
        assert "simultaneously on multiple surfaces" in initial.infeasible_reason
        assert "simultaneously on multiple surfaces" in direct.infeasible_reason


class TestCLI:
    def test_cli_generates_output(self, tmp_path: Path, rules: dict) -> None:
        out = tmp_path / "tasks.jsonl"
        rc = main(["--rules", str(RULES_PATH), "--output", str(out), "--no-images"])
        assert rc == 0
        assert out.exists()
        lines = [json.loads(l) for l in out.read_text().strip().splitlines()]
        assert len(lines) == 55
        assert all("task_id" in t for t in lines)

    def test_cli_no_images(self, tmp_path: Path) -> None:
        out = tmp_path / "tasks.jsonl"
        main(["--rules", str(RULES_PATH), "--output", str(out), "--no-images"])
        lines = [json.loads(l) for l in out.read_text().strip().splitlines()]
        assert all(t.get("image_path") is None for t in lines)
