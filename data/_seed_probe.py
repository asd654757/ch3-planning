#!/usr/bin/env python3
"""Diagnostic: how much of the Fair-100 end-to-end outcome is seed noise?

Executes one fixed plan for each of four frozen points under N different
arm-name salts, which is the only knob ``stable_seed`` exposes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/root/autodl-tmp/ch3-planning")
from ch3.capability.registry import CapabilityRegistry
from ch3.schema.model_plan import ModelPlan
from scripts.sim_compare_baselines import execute_multiskill_plan

POINTS = [
    ("multiskill_pick_place_001", 13, "BFS"),
    ("multiskill_pick_place_010", 22, "BFS"),
    ("multiskill_pick_place_007", 31, "ROUTED"),
    ("multiskill_pick_place_008", 8, "ROUTED"),
]
SALTS = 8

registry = CapabilityRegistry.from_yaml("config/capability_registry.yaml")
cases = {
    (str(row["task_id"]), int(row["seed"]), row["mode"]): row
    for row in map(
        json.loads,
        Path(
            "data/reports/symbolic_planner_fair100_20260926_044237/"
            "symbolic_planner_cases.jsonl"
        ).open(encoding="utf-8"),
    )
    if row.get("record_type") == "symbolic_planner_case"
}
routed = {}
for line in Path(
    "data/collections/fair_routed_v2_20260916_102135.jsonl"
).read_text(encoding="utf-8").splitlines():
    row = json.loads(line)
    if row.get("record_type") == "fair_routed_v2_case":
        routed[(str(row["task_id"]), int(row["seed"]))] = row

for task_id, seed, origin in POINTS:
    if origin == "BFS":
        actions = cases[(task_id, seed, "BFS_EXEC")]["suffix_actions"]
    else:
        actions = routed[(task_id, seed)]["plan"]["actions"]
    plan = ModelPlan.model_validate({"actions": actions})
    outcomes = []
    for salt in range(SALTS):
        success, _prims, _tasks = execute_multiskill_plan(
            plan,
            registry,
            (task_id, seed, "SEEDPROBE"),
            f"SEEDPROBE_{salt}",
            max_steps=300,
            observation_size=224,
        )
        outcomes.append(int(bool(success)))
    print(
        f"{origin:6} {task_id} seed={seed} plan={[a['skill'] for a in actions]} "
        f"successes={sum(outcomes)}/{SALTS} pattern={''.join(map(str,outcomes))}",
        flush=True,
    )
