"""Tests for the formal task generator (stage 3, priority 2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from ch3.tasks.generator import TaskGenerator, build_arg_parser, main


RULES_PATH = Path(__file__).parent.parent / "config" / "task_rules.yaml"


@pytest.fixture()
def rules() -> dict:
    with open(RULES_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestTaskGenerator:
    def test_generates_40_tasks(self, rules: dict) -> None:
        gen = TaskGenerator(rules, seed=42)
        tasks = gen.generate_all()
        assert len(tasks) == 40

    def test_difficulty_distribution(self, rules: dict) -> None:
        gen = TaskGenerator(rules, seed=42)
        tasks = gen.generate_all()
        counts = {"easy": 0, "medium": 0, "hard": 0, "infeasible": 0}
        for t in tasks:
            counts[t["difficulty"]] += 1
        assert counts == {"easy": 10, "medium": 10, "hard": 15, "infeasible": 5}

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


class TestCLI:
    def test_cli_generates_output(self, tmp_path: Path, rules: dict) -> None:
        out = tmp_path / "tasks.jsonl"
        rc = main(["--rules", str(RULES_PATH), "--output", str(out), "--no-images"])
        assert rc == 0
        assert out.exists()
        lines = [json.loads(l) for l in out.read_text().strip().splitlines()]
        assert len(lines) == 40
        assert all("task_id" in t for t in lines)

    def test_cli_no_images(self, tmp_path: Path) -> None:
        out = tmp_path / "tasks.jsonl"
        main(["--rules", str(RULES_PATH), "--output", str(out), "--no-images"])
        lines = [json.loads(l) for l in out.read_text().strip().splitlines()]
        assert all(t.get("image_path") is None for t in lines)
