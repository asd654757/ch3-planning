"""Rule-based formal task generator for the frozen experiment protocol.

Generates 40 tasks (10 easy / 10 medium / 15 hard / 5 infeasible) with
guaranteed solvability (or intentional infeasibility). Each task is output
as a JSONL record compatible with ch3.vlm.collector.

Usage:
    python -m ch3.tasks.generator \
        --output data/scenarios/formal_tasks_v1.jsonl \
        --rules config/task_rules.yaml \
        --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
import struct
import zlib
from pathlib import Path
from typing import Any, Optional

import yaml

from ch3.schema.model_plan import Arm, ModelPlanAction, Skill
from ch3.state.simulator import step
from ch3.state.world_state import WorldState

# ---------------------------------------------------------------------------
# Object pools
# ---------------------------------------------------------------------------
COLOR_RGB = {
    "red": (220, 40, 40),
    "blue": (40, 60, 220),
    "green": (40, 190, 60),
    "yellow": (240, 220, 30),
    "orange": (240, 140, 20),
    "purple": (160, 40, 200),
    "cyan": (20, 200, 200),
    "pink": (240, 100, 160),
    "white": (235, 235, 235),
    "brown": (150, 100, 50),
}

# ---------------------------------------------------------------------------
# Simple PNG generator (no external dependencies)
# ---------------------------------------------------------------------------

def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    c = chunk_type + data
    return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))


def _encode_png(width: int, height: int, pixels: list[list[tuple[int, int, int]]]) -> bytes:
    header = b"\x89PNG\r\n\x1a\n"
    ihdr = _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    raw = b""
    for row in pixels:
        raw += b"\x00"
        for r, g, b in row:
            raw += struct.pack("BBB", r, g, b)
    idat = _png_chunk(b"IDAT", zlib.compress(raw))
    iend = _png_chunk(b"IEND", b"")
    return header + ihdr + idat + iend


def draw_scene(objects: list[dict[str, Any]]) -> bytes:
    """Draw a top-down scene with colored objects on a light table."""
    W, H = 320, 200
    pixels: list[list[tuple[int, int, int]]] = [
        [(235, 220, 200)] * W for _ in range(H)
    ]
    for obj in objects:
        r, g, b = obj["rgb"]
        cx, cy, half = obj["px"], obj["py"], obj["size"]
        for dy in range(-half, half + 1):
            for dx in range(-half, half + 1):
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < W and 0 <= ny < H:
                    pixels[ny][nx] = (r, g, b)
    return _encode_png(W, H, pixels)


# ---------------------------------------------------------------------------
# Instruction templates
# ---------------------------------------------------------------------------

def _goal_phrase(color: str, shape: str, target: str, arm: Optional[str] = None) -> str:
    base = f"the {color} {shape}"
    if target == "table":
        phrase = f"put {base} on the table"
    else:
        phrase = f"put {base} in the {target}"
    if arm:
        phrase = f"use the {arm} arm to {phrase}"
    return phrase


def _ambiguous_phrase(color: str, shape: str, target: str) -> str:
    """Deliberately vague instruction (hard tasks)."""
    style = random.choice([
        f"Move it to the {target}.",
        f"Sort that one into the {target}.",
        f"Place it where it belongs — in the {target}.",
    ])
    return style


# ---------------------------------------------------------------------------
# Task generation
# ---------------------------------------------------------------------------

class TaskGenerator:
    def __init__(self, rules: dict[str, Any], seed: int = 42) -> None:
        self.rules = rules
        self.rng = random.Random(seed)
        self.used_names: set[str] = set()
        self.used_ids: set[str] = set()

    def _fresh_name(self, color: str, shape: str) -> str:
        """Generate a unique object name."""
        for i in range(100):
            name = f"{color}_{shape}_{i}"
            if name not in self.used_names:
                self.used_names.add(name)
                return name
        raise RuntimeError("Object name pool exhausted")

    def _pick(self, pool: list[str]) -> str:
        return self.rng.choice(pool)

    def _pick_range(self, spec: Any) -> int:
        if isinstance(spec, list):
            return self.rng.randint(spec[0], spec[1])
        return spec

    def _assign_positions(self, n: int) -> list[tuple[int, int]]:
        """Assign non-overlapping positions for n objects."""
        positions = []
        used: set[tuple[int, int]] = set()
        min_dist = 36
        for _ in range(n * 100):
            x = self.rng.randint(40, 280)
            y = self.rng.randint(30, 170)
            if all((x - ux) ** 2 + (y - uy) ** 2 >= min_dist ** 2 for ux, uy in used):
                positions.append((x, y))
                used.add((x, y))
                if len(positions) == n:
                    break
        while len(positions) < n:
            positions.append((40 + len(positions) * 30, 100))
        return positions

    def _generate_feasible_task(
        self,
        difficulty: str,
        rules: dict[str, Any],
        task_index: int,
    ) -> dict[str, Any]:
        num_goals = self._pick_range(rules["num_goal_objects"])
        num_containers = self._pick_range(rules["num_containers"])
        num_distractors = self._pick_range(rules["num_distractors"])
        dual_arm = rules["dual_arm"]
        ambiguous = rules["ambiguous_instruction"]

        # Pick colors and shapes
        colors = self.rng.sample(list(COLOR_RGB.keys()), num_goals + num_containers + num_distractors)
        shapes = [self._pick(rules.get("shapes", ["cube", "block"])) for _ in range(num_goals)]
        container_shapes = [self._pick(rules.get("containers", ["tray", "box", "bowl"])) for _ in range(num_containers)]
        distractor_shapes = [self._pick(rules.get("shapes", ["cube", "block"])) for _ in range(num_distractors)]

        # Build object list
        goal_objects = []
        for i in range(num_goals):
            name = self._fresh_name(colors[i], shapes[i])
            goal_objects.append(name)

        containers = []
        for i in range(num_containers):
            name = self._fresh_name(colors[num_goals + i], container_shapes[i])
            containers.append(name)

        distractors = []
        for i in range(num_distractors):
            name = self._fresh_name(colors[num_goals + num_containers + i], distractor_shapes[i])
            distractors.append(name)

        all_objects = goal_objects + containers + distractors

        # Initial state
        at: dict[str, str] = {}
        for obj in all_objects:
            at[obj] = "table"

        # Goals
        goal_facts = []
        for i, obj in enumerate(goal_objects):
            if i < len(containers):
                tgt = containers[i]
            else:
                tgt = self.rng.choice(containers)
            goal_facts.append(f"on({obj}, {tgt})")

        # Verify solvability using the simulator
        solvable = self._verify_solvability(all_objects, at, goal_facts, dual_arm)

        # Instruction
        instruction = self._make_instruction(
            goal_objects, goal_facts, colors, shapes, containers,
            dual_arm, ambiguous, difficulty
        )

        task_id = f"{difficulty}_{task_index:03d}"

        return {
            "task_id": task_id,
            "difficulty": difficulty,
            "instruction": instruction,
            "objects": all_objects,
            "initial_state": {"at": at, "holding": {}},
            "goal": {"facts": goal_facts},
            "solvable": solvable,
            "hard_factors": {
                "multi_distractors": num_distractors >= 2,
                "dual_arm": dual_arm and num_goals >= 2,
                "multi_step": num_goals >= 2,
                "semantic_ambiguity": ambiguous,
            },
        }

    def _generate_infeasible_task(
        self,
        rules: dict[str, Any],
        task_index: int,
    ) -> dict[str, Any]:
        """Generate a task whose goal references a non-existent target."""
        num_goals = self._pick_range(rules["num_goal_objects"])
        num_containers = self._pick_range(rules["num_containers"])

        colors = self.rng.sample(list(COLOR_RGB.keys()), num_goals + num_containers + 1)
        shapes = [self._pick(rules.get("shapes", ["cube", "block"])) for _ in range(num_goals)]
        container_shapes = [self._pick(rules.get("containers", ["tray", "box", "bowl"])) for _ in range(num_containers)]

        goal_objects = []
        for i in range(num_goals):
            name = self._fresh_name(colors[i], shapes[i])
            goal_objects.append(name)

        containers = []
        for i in range(num_containers):
            name = self._fresh_name(colors[num_goals + i], container_shapes[i])
            containers.append(name)

        # Phantom object (in scene but not in objects list → goal references it)
        phantom_color = colors[-1]
        phantom_name = f"{phantom_color}_cube_99"

        all_objects = goal_objects + containers
        at = {obj: "table" for obj in all_objects}

        # Goal references the phantom object (not in scene → infeasible)
        goal_facts = [f"on({phantom_name}, {containers[0]})"]
        instruction = f"Put the {phantom_color} cube into the {containers[0]}."

        task_id = f"infeasible_{task_index:03d}"
        return {
            "task_id": task_id,
            "difficulty": "infeasible",
            "instruction": instruction,
            "objects": all_objects,
            "initial_state": {"at": at, "holding": {}},
            "goal": {"facts": goal_facts},
            "solvable": False,
            "hard_factors": {},
        }

    def _verify_solvability(
        self,
        objects: list[str],
        at: dict[str, str],
        goal_facts: list[str],
        dual_arm: bool,
    ) -> bool:
        """Use the simulator to verify a greedy plan can achieve the goal."""
        state = WorldState(objects=set(objects), at=dict(at))
        arms = [Arm.LEFT, Arm.RIGHT] if dual_arm else [Arm.LEFT]
        arm_idx = 0

        for fact in goal_facts:
            # Parse "on(obj, target)"
            inner = fact[3:-1]
            obj, tgt = [s.strip() for s in inner.split(",", 1)]

            # If obj is stacked on something, move the blocker first
            if at.get(obj, "table") != "table":
                blocker = at[obj]
                if blocker in objects:
                    # Move the blocker to table first
                    arm = arms[arm_idx % len(arms)]
                    s, ok, _, _ = step(state, ModelPlanAction(
                        step_id=1, skill=Skill.PICK, object_id=blocker, arm=arm
                    ))
                    if not ok:
                        return False
                    state = s
                    s, ok, _, _ = step(state, ModelPlanAction(
                        step_id=2, skill=Skill.PLACE, object_id=blocker,
                        target_id="table", arm=arm
                    ))
                    if not ok:
                        return False
                    state = s
                    arm_idx += 1

            # Pick the goal object
            arm = arms[arm_idx % len(arms)]
            s, ok, _, _ = step(state, ModelPlanAction(
                step_id=1, skill=Skill.PICK, object_id=obj, arm=arm
            ))
            if not ok:
                return False
            state = s

            # Place it
            s, ok, _, _ = step(state, ModelPlanAction(
                step_id=2, skill=Skill.PLACE, object_id=obj, target_id=tgt, arm=arm
            ))
            if not ok:
                return False
            state = s
            arm_idx += 1

        # Check goal satisfaction
        facts_now = state.facts() | state.empty_hand_facts([a.value for a in arms])
        return all(f in facts_now for f in goal_facts)

    def _make_instruction(
        self,
        goal_objects: list[str],
        goal_facts: list[str],
        colors: list[str],
        shapes: list[str],
        containers: list[str],
        dual_arm: bool,
        ambiguous: bool,
        difficulty: str,
    ) -> str:
        parts = []
        for i, fact in enumerate(goal_facts):
            inner = fact[3:-1]
            obj, tgt = [s.strip() for s in inner.split(",", 1)]
            color = obj.rsplit("_", 2)[0] if "_" in obj else obj
            shape = "cube"
            arm = None
            if dual_arm and i < len(goal_objects):
                arm = "left" if i % 2 == 0 else "right"
            if ambiguous and self.rng.random() < 0.4:
                parts.append(_ambiguous_phrase(color, shape, tgt))
            else:
                parts.append(_goal_phrase(color, shape, tgt, arm))
        return "; then ".join(parts).capitalize() + "."

    # -----------------------------------------------------------------------
    def generate_all(self) -> list[dict[str, Any]]:
        tasks = []
        idx = 0
        for diff in ["easy", "medium", "hard"]:
            rules = self.rules[diff]
            for i in range(rules["count"]):
                t = self._generate_feasible_task(diff, rules, idx)
                t["solvable"] = self._verify_solvability(
                    t["objects"], t["initial_state"]["at"], t["goal"]["facts"],
                    rules.get("dual_arm", False)
                )
                tasks.append(t)
                idx += 1
        # Infeasible
        rules = self.rules["infeasible"]
        for i in range(rules["count"]):
            t = self._generate_infeasible_task(rules, idx)
            tasks.append(t)
            idx += 1
        return tasks


def generate_image(task: dict[str, Any], output_dir: str | Path, seed: int = 0) -> str:
    """Generate a simple top-down PNG for a task and return the path."""
    objects = []
    rng = random.Random(seed)
    positions = _assign_positions_standalone(rng, len(task["objects"]))
    for i, (name, (px, py)) in enumerate(zip(task["objects"], positions)):
        color_name = name.rsplit("_", 2)[0] if "_" in name else "gray"
        rgb = COLOR_RGB.get(color_name, (128, 128, 128))
        size = 18 if any(name.endswith(c) for c in ["tray", "box", "bowl"]) else 10
        objects.append({"px": px, "py": py, "size": size, "rgb": rgb})
    png_bytes = draw_scene(objects)
    out = Path(output_dir) / f"{task['task_id']}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png_bytes)
    return str(out)


def _assign_positions_standalone(rng: random.Random, n: int) -> list[tuple[int, int]]:
    """Assign non-overlapping positions for n objects (no class needed)."""
    positions = []
    used: set[tuple[int, int]] = set()
    min_dist = 36
    for _ in range(n * 100):
        x = rng.randint(40, 280)
        y = rng.randint(30, 170)
        if all((x - ux) ** 2 + (y - uy) ** 2 >= min_dist ** 2 for ux, uy in used):
            positions.append((x, y))
            used.add((x, y))
            if len(positions) == n:
                break
    while len(positions) < n:
        positions.append((40 + len(positions) * 30, 100))
    return positions


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rules", default="config/task_rules.yaml")
    p.add_argument("--output", default="data/scenarios/formal_tasks_v1.jsonl")
    p.add_argument("--image-dir", default="data/scenes/images/generated")
    p.add_argument("--seed", type=int, default=None, help="Override rules seed")
    p.add_argument("--no-images", action="store_true")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    with open(args.rules, "r", encoding="utf-8") as f:
        rules = yaml.safe_load(f)

    seed = args.seed if args.seed is not None else rules.get("seed", 42)
    gen = TaskGenerator(rules, seed=seed)
    tasks = gen.generate_all()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    stats: dict[str, int] = {"easy": 0, "medium": 0, "hard": 0, "infeasible": 0}
    solvable_count = 0

    with open(output, "w", encoding="utf-8") as f:
        for task in tasks:
            if not args.no_images:
                img_path = generate_image(task, args.image_dir)
                task["image_path"] = img_path
            else:
                task["image_path"] = None
            f.write(json.dumps(task, ensure_ascii=False) + "\n")
            stats[task["difficulty"]] = stats.get(task["difficulty"], 0) + 1
            if task.get("solvable"):
                solvable_count += 1

    total = len(tasks)
    print(f"Generated {total} tasks → {output}")
    print(f"  easy: {stats['easy']}  medium: {stats['medium']}  hard: {stats['hard']}  infeasible: {stats['infeasible']}")
    print(f"  solvable: {solvable_count}/{total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
