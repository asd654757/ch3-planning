#!/usr/bin/env python3
"""Scene-re-seeded stability study: symbolic planner vs ROUTED plans (C5 support).

The frozen Fair-100 protocol scores one MetaWorld episode per arm, and the
executor hashes the arm name into ``stable_seed``, so each arm is run in its own
re-randomised initial scene.  Even after both arms are put on one shared scene
salt (100/100 each), a single draw per plan says nothing about robustness: a
probe that re-executed one *fixed* plan under 8 different salts flipped outcomes
(7/8 or 8/8 successes).  Cross-arm single-draw differences are therefore inside
scene noise and are not paired evidence.

This study removes that confound.  For every frozen point it takes the plan the
symbolic planner produced (``BFS_EXEC``) and the plan ROUTED produced, and
executes both under the same N scene salts -- same seed for both arms, so both
see the same initial object placement.  The result is a paired plan-level
comparison plus a per-plan stability spectrum.

Zero VLM calls.  Frozen inputs are read, never written.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import os

os.environ.setdefault("MUJOCO_GL", "egl")

from ch3.capability.registry import CapabilityRegistry
from ch3.schema.model_plan import ModelPlan
from scripts.sim_compare_baselines import execute_multiskill_plan
from scripts.symbolic_planner_baseline import is_sim_executable


RECORD = "reseed_stability_cell"
SCHEMA = "2026-09-26-reseed-stability-v1"
ARMS = ("SYMBOLIC", "ROUTED")
HEADLINE_MODE = "BFS_EXEC"


def exact_mcnemar(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(0, min(b, c) + 1))
    return min(1.0, 2.0 * tail * (0.5**n))


def load_routed_plans(path: Path) -> dict[tuple[str, int], dict[str, Any]]:
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("record_type") != "fair_routed_v2_case":
            continue
        plan = row.get("plan")
        if not plan:
            continue
        out[(str(row["task_id"]), int(row["seed"]))] = {
            "actions": plan["actions"],
            "single_draw_success": bool(row["final_success"]),
        }
    return out


def load_symbolic_plans(path: Path) -> dict[tuple[str, int], dict[str, Any]]:
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if (
            row.get("record_type") != "symbolic_planner_case"
            or row.get("mode") != HEADLINE_MODE
        ):
            continue
        out[(str(row["task_id"]), int(row["seed"]))] = {
            "actions": row["suffix_actions"],
            "single_draw_success": bool(row["final_success"]),
            "task_family": row["task_family"],
        }
    return out


def done_cells(path: Path) -> set[tuple[str, int, str, int]]:
    if not path.exists():
        return set()
    cells: set[tuple[str, int, str, int]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("record_type") == RECORD:
            cells.add(
                (str(row["task_id"]), int(row["seed"]), str(row["arm"]), int(row["salt"]))
            )
    return cells


def summarize(path: Path, points: int, salts: int) -> dict[str, Any]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("record_type") == RECORD
    ]
    by_arm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_arm[str(row["arm"])].append(row)

    per_arm: dict[str, Any] = {}
    for arm in ARMS:
        cells = by_arm[arm]
        grouped: dict[tuple[str, int], list[bool]] = defaultdict(list)
        for row in cells:
            grouped[(str(row["task_id"]), int(row["seed"]))].append(
                bool(row["sim_success"])
            )
        spectrum: dict[str, int] = defaultdict(int)
        for key, outcomes in grouped.items():
            spectrum[f"{sum(outcomes)}/{salts}"] += 1
        executed = sum(len(v) >= salts for v in grouped.values())
        per_arm[arm] = {
            "points_with_full_salt_sweep": executed,
            "cell_success_rate": (
                sum(bool(row["sim_success"]) for row in cells) / len(cells)
                if cells
                else None
            ),
            "cells": len(cells),
            "points_always_succeed": sum(
                1 for v in grouped.values() if all(v) and len(v) >= salts
            ),
            "points_always_fail": sum(
                1 for v in grouped.values() if not any(v) and len(v) >= salts
            ),
            "points_mixed": sum(
                1
                for v in grouped.values()
                if any(v) and not all(v) and len(v) >= salts
            ),
            "success_spectrum": dict(sorted(spectrum.items())),
        }

    paired: dict[tuple[str, int, int], dict[str, bool]] = defaultdict(dict)
    for row in rows:
        paired[(str(row["task_id"]), int(row["seed"]), int(row["salt"]))][
            str(row["arm"])
        ] = bool(row["sim_success"])
    complete = {k: v for k, v in paired.items() if set(v) == set(ARMS)}
    b = sum(1 for v in complete.values() if v["SYMBOLIC"] and not v["ROUTED"])
    c = sum(1 for v in complete.values() if not v["SYMBOLIC"] and v["ROUTED"])
    both = sum(1 for v in complete.values() if v["SYMBOLIC"] and v["ROUTED"])
    point_level: dict[str, int] = defaultdict(int)
    symbolic_only_points: list[str] = []
    routed_only_points: list[str] = []
    grouped: dict[str, dict[str, list[bool]]] = {arm: defaultdict(list) for arm in ARMS}
    for row in rows:
        grouped[str(row["arm"])][(str(row["task_id"]), int(row["seed"]))].append(
            bool(row["sim_success"])
        )
    for key, sym in grouped["SYMBOLIC"].items():
        rt = grouped["ROUTED"].get(key, [])
        if len(sym) < salts or len(rt) < salts:
            continue
        sym_majority = sum(sym) * 2 > salts
        rt_majority = sum(rt) * 2 > salts
        if sym_majority and not rt_majority:
            point_level["symbolic_only_reliable"] += 1
            symbolic_only_points.append(f"{key[0]}/{key[1]}")
        elif rt_majority and not sym_majority:
            point_level["routed_only_reliable"] += 1
            routed_only_points.append(f"{key[0]}/{key[1]}")
        elif sym_majority and rt_majority:
            point_level["both_reliable"] += 1
        else:
            point_level["neither_reliable"] += 1

    plans: dict[tuple[str, int], dict[str, tuple[Any, ...]]] = defaultdict(dict)
    outcomes: dict[tuple[str, int], dict[str, list[Optional[bool]]]] = defaultdict(
        lambda: {arm: [None] * salts for arm in ARMS}
    )
    families: dict[tuple[str, int], str] = {}
    for row in rows:
        key = (str(row["task_id"]), int(row["seed"]))
        salt = int(row["salt"])
        arm = str(row["arm"])
        if salt >= salts:
            continue
        outcomes[key][arm][salt] = bool(row["sim_success"])
        plans[key][arm] = tuple(tuple(a) for a in row["actions"])
        families[key] = str(row["task_family"])

    identical = {
        k for k, v in plans.items() if len(v) == len(ARMS) and v[ARMS[0]] == v[ARMS[1]]
    }
    plan_identity_split: dict[str, Any] = {}
    for name, keys in (
        ("identical_plan", sorted(identical)),
        ("differing_plan", sorted(set(outcomes) - identical)),
    ):
        cells = 0
        successes = 0
        discordant = 0
        fragile: list[str] = []
        fragile_by_family: dict[str, int] = defaultdict(int)
        for key in keys:
            hits = 0
            for salt in range(salts):
                sym = outcomes[key]["SYMBOLIC"][salt]
                routed = outcomes[key]["ROUTED"][salt]
                if sym is None or routed is None:
                    continue
                cells += 1
                successes += int(sym)
                discordant += int(sym != routed)
                hits += int(sym)
            if 0 < hits < salts:
                fragile.append(f"{key[0]}/{key[1]}")
                fragile_by_family[families[key]] += 1
        plan_identity_split[name] = {
            "points": len(keys),
            "symbolic_cells": cells,
            "symbolic_cell_successes": successes,
            "symbolic_cell_success_rate": successes / cells if cells else None,
            "discordant_cells": discordant,
            "scene_fragile_points": len(fragile),
            "scene_fragile_by_family": dict(sorted(fragile_by_family.items())),
            "scene_fragile_list": sorted(fragile),
        }

    return {
        "record_type": "reseed_stability_summary",
        "plan_identity_split": plan_identity_split,
        "schema_version": SCHEMA,
        "points_in_scope": points,
        "salts": salts,
        "matched_cells": len(complete),
        "both_arms_cell_success": both,
        "only_SYMBOLIC_cell_success": b,
        "only_ROUTED_cell_success": c,
        "neither_cell_success": len(complete) - both - b - c,
        "mcnemar_exact_p_value": exact_mcnemar(b, c),
        "per_arm": per_arm,
        "point_level_majority": dict(point_level),
        "symbolic_only_reliable_points": sorted(symbolic_only_points),
        "routed_only_reliable_points": sorted(routed_only_points),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbolic-cases", required=True)
    parser.add_argument(
        "--routed", default="data/collections/fair_routed_v2_20260916_102135.jsonl"
    )
    parser.add_argument("--registry", default="config/capability_registry.yaml")
    parser.add_argument("--output", required=True)
    parser.add_argument("--salts", type=int, default=5)
    parser.add_argument("--max-steps-per-primitive", type=int, default=300)
    parser.add_argument("--observation-size", type=int, default=224)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--summary-only", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    output_path = Path(args.output)
    if output_path.exists() and not (args.resume or args.summary_only):
        raise FileExistsError(f"Refusing to overwrite {output_path}; use --resume.")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    symbolic = load_symbolic_plans(Path(args.symbolic_cases))
    routed = load_routed_plans(Path(args.routed))
    registry = CapabilityRegistry.from_yaml(args.registry)

    in_scope = []
    skipped = []
    for key in sorted(set(symbolic) & set(routed), key=lambda item: (item[0], item[1])):
        sym_ok = is_sim_executable(symbolic[key]["actions"])
        rt_ok = is_sim_executable(routed[key]["actions"])
        if sym_ok and rt_ok:
            in_scope.append(key)
        else:
            skipped.append((key, sym_ok, rt_ok))

    done = done_cells(output_path) if args.resume else set()
    print(
        json.dumps(
            {
                "record_type": "reseed_stability_start",
                "points_in_scope": len(in_scope),
                "points_skipped_not_executable_shape": len(skipped),
                "salts": args.salts,
                "expected_executions": len(in_scope) * args.salts * 2,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    for key, sym_ok, rt_ok in skipped:
        print(
            json.dumps(
                {
                    "record_type": "reseed_stability_skipped",
                    "task_id": key[0],
                    "seed": key[1],
                    "symbolic_shape_ok": sym_ok,
                    "routed_shape_ok": rt_ok,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    started = time.time()
    pending = [] if args.summary_only else in_scope
    with output_path.open("a", encoding="utf-8") as handle:
        for index, (task_id, seed) in enumerate(pending, 1):
            for salt in range(args.salts):
                for arm in ARMS:
                    if (task_id, seed, arm, salt) in done:
                        continue
                    actions = (
                        symbolic[(task_id, seed)]["actions"]
                        if arm == "SYMBOLIC"
                        else routed[(task_id, seed)]["actions"]
                    )
                    plan = ModelPlan.model_validate({"actions": actions})
                    # Same salt for both arms => identical stable_seed => the same
                    # initial object placement, which is the point of the study.
                    success, _prims, _tasks = execute_multiskill_plan(
                        plan,
                        registry,
                        (task_id, seed, "RESEED"),
                        f"RESEED_{salt}",
                        max_steps=args.max_steps_per_primitive,
                        observation_size=args.observation_size,
                    )
                    record = {
                        "record_type": RECORD,
                        "schema_version": SCHEMA,
                        "task_id": task_id,
                        "seed": seed,
                        "task_family": symbolic[(task_id, seed)]["task_family"],
                        "arm": arm,
                        "salt": salt,
                        "sim_success": bool(success),
                        "single_draw_protocol_success": (
                            symbolic[(task_id, seed)]["single_draw_success"]
                            if arm == "SYMBOLIC"
                            else routed[(task_id, seed)]["single_draw_success"]
                        ),
                        "actions": [
                            [item["skill"], item["object_id"], item["target_id"], item["arm"]]
                            for item in actions
                        ],
                    }
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    handle.flush()
                print(
                    f"[reseed] point={index}/{len(in_scope)} task={task_id} "
                    f"seed={seed} salt={salt} done",
                    flush=True,
                )

    summary = summarize(output_path, len(in_scope), args.salts)
    sidecar = output_path.with_name("reseed_stability_summary.json")
    if args.summary_only:
        previous = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
        summary["summarize_only"] = True
        summary["elapsed_s"] = previous.get("elapsed_s")
        summary["cells_completed_at_utc"] = previous.get("completed_at_utc")
    else:
        summary["elapsed_s"] = time.time() - started
    summary["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    summary["symbolic_cases"] = args.symbolic_cases
    summary["routed"] = args.routed
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    sidecar = output_path.with_name("reseed_stability_summary.json")
    sidecar.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
