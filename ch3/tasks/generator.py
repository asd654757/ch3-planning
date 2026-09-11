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
    "black": (30, 30, 30),
    "gray": (128, 128, 128),
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
    """Deliberately vague instruction (hard tasks). No color/shape mention."""
    styles = [
        f"Move it to the {target}.",
        f"Sort that one into the {target}.",
        f"Place it where it belongs — in the {target}.",
        f"Put that one in the {target} too.",
        f"Same thing for that one — into the {target}.",
        f"And that other one goes to the {target}.",
        f"Then handle the remaining one — {target}.",
    ]
    return random.choice(styles)


# ---------------------------------------------------------------------------
# Task generation
# ---------------------------------------------------------------------------

class TaskGenerator:
    def __init__(self, rules: dict[str, Any], seed: int = 42) -> None:
        self.rules = rules
        self.rng = random.Random(seed)
        self.used_names: set[str] = set()
        self.used_ids: set[str] = set()
        self.object_counter: int = 0

    def _fresh_name(self, color: str, shape: str) -> str:
        """Generate a globally unique object name.

        The counter must be independent of (color, shape): otherwise a base
        name already used by an earlier scene would make `_fresh_name` skip
        to a higher suffix and accidentally recreate the same color/shape
        description later within the current scene.
        """
        name = f"{color}_{shape}_{self.object_counter}"
        self.object_counter += 1
        self.used_names.add(name)
        return name

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
        ambiguity_rate = rules.get("ambiguity_rate", 0.4)

        # Pick descriptions first. When enabled, every object in the scene
        # has a unique (color, shape) pair so natural-language phrases such
        # as "the pink cube" can refer to exactly one visible object.
        unique_descriptions = bool(rules.get("unique_descriptions", False))
        total_needed = num_goals + num_containers + num_distractors
        if unique_descriptions:
            movable_shapes = rules.get("shapes", ["cube", "block"])
            container_shapes_pool = rules.get("containers", ["tray", "box", "bowl"])
            # Goals and distractors must both draw from the same movable
            # shape pool so a distractor can look like a potential goal.
            # Containers use their own shape pool; color may repeat across
            # roles, but the full (color, shape) description remains unique.
            movable_pool = [
                (color, shape)
                for color in COLOR_RGB
                for shape in movable_shapes
            ]
            container_pool = [
                (color, shape)
                for color in COLOR_RGB
                for shape in container_shapes_pool
            ]
            movable_needed = num_goals + num_distractors
            if movable_needed > len(movable_pool):
                raise RuntimeError(
                    "Not enough unique (color, shape) descriptions for scene"
                )
            if num_containers > len(container_pool):
                raise RuntimeError(
                    "Not enough unique (color, container-shape) descriptions"
                )
            movable_descriptions = self.rng.sample(movable_pool, movable_needed)
            container_descriptions = self.rng.sample(
                container_pool, num_containers
            )
            goal_descriptions = movable_descriptions[:num_goals]
            distractor_descriptions = movable_descriptions[num_goals:]
        else:
            # Pick colors and shapes
            color_pool = list(COLOR_RGB.keys())
            if total_needed <= len(color_pool):
                colors = self.rng.sample(color_pool, total_needed)
            else:
                # Allow color reuse if pool is exhausted
                colors = [self.rng.choice(color_pool) for _ in range(total_needed)]
            shapes = [self._pick(rules.get("shapes", ["cube", "block"])) for _ in range(num_goals)]
            container_shapes = [self._pick(rules.get("containers", ["tray", "box", "bowl"])) for _ in range(num_containers)]
            distractor_shapes = [self._pick(rules.get("shapes", ["cube", "block"])) for _ in range(num_distractors)]
            goal_descriptions = list(zip(colors, shapes))
            container_descriptions = list(zip(colors[num_goals:], container_shapes))
            distractor_descriptions = list(zip(colors[num_goals + num_containers:], distractor_shapes))

        # Build object list
        goal_objects = []
        for color, shape in goal_descriptions:
            name = self._fresh_name(color, shape)
            goal_objects.append(name)

        containers = []
        for color, shape in container_descriptions:
            name = self._fresh_name(color, shape)
            containers.append(name)

        distractors = []
        for color, shape in distractor_descriptions:
            name = self._fresh_name(color, shape)
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
            goal_objects, goal_facts,
            [color for color, _ in goal_descriptions],
            [shape for _, shape in goal_descriptions],
            containers,
            dual_arm, ambiguous, difficulty, ambiguity_rate
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

    def _generate_attribute_grouped_task(
        self,
        rules: dict[str, Any],
        task_index: int,
    ) -> dict[str, Any]:
        """Generate a task defined by an object attribute, not object IDs.

        The target objects intentionally share one color but differ in shape.
        This requires the VLM to instantiate a natural-language group ("all
        red objects") into multiple closed-world object IDs.
        """
        num_containers = self._pick_range(rules["num_containers"])
        num_distractors = self._pick_range(rules["num_distractors"])

        shapes = rules.get("shapes", ["cube", "block", "wedge", "sphere"])
        num_groups = self._pick_range(rules.get("num_groups", 1))
        objects_per_group = self._pick_range(
            rules.get("objects_per_group", rules.get("num_goal_objects", 2))
        )
        if objects_per_group > len(shapes):
            raise RuntimeError("attribute_grouped needs enough distinct shapes")

        group_colors = rules.get("group_colors", ["red", "blue", "green"])
        if num_groups > len(group_colors):
            raise RuntimeError("attribute_grouped needs enough distinct group colors")
        target_colors = self.rng.sample(group_colors, num_groups)
        goal_descriptions: list[tuple[str, str]] = []
        group_sizes: dict[str, int] = {}
        for target_color in target_colors:
            selected_shapes = self.rng.sample(shapes, objects_per_group)
            goal_descriptions.extend((target_color, shape) for shape in selected_shapes)
            group_sizes[target_color] = objects_per_group
        num_goals = len(goal_descriptions)

        movable_pool = [
            (color, shape)
            for color in COLOR_RGB
            for shape in shapes
            if color not in target_colors
        ]
        distractor_descriptions = self.rng.sample(movable_pool, num_distractors)

        container_shapes = rules.get("containers", ["tray", "box", "bowl"])
        container_pool = [
            (color, shape)
            for color in COLOR_RGB
            for shape in container_shapes
            if color not in target_colors
        ]
        container_descriptions = self.rng.sample(container_pool, num_containers)

        goal_objects = [self._fresh_name(color, shape) for color, shape in goal_descriptions]
        containers = [self._fresh_name(color, shape) for color, shape in container_descriptions]
        distractors = [self._fresh_name(color, shape) for color, shape in distractor_descriptions]
        all_objects = goal_objects + containers + distractors
        at = {obj: "table" for obj in all_objects}

        goal_facts = []
        description_cursor = 0
        for group_idx, target_color in enumerate(target_colors):
            target_container = containers[group_idx % len(containers)]
            for _ in range(group_sizes[target_color]):
                obj = goal_objects[description_cursor]
                goal_facts.append(f"on({obj}, {target_container})")
                description_cursor += 1
        solvable = self._verify_solvability(
            all_objects, at, goal_facts, bool(rules.get("dual_arm", False))
        )
        instruction_parts = []
        for group_idx, target_color in enumerate(target_colors):
            container_shape = containers[group_idx % len(containers)].rsplit("_", 2)[1]
            instruction_parts.append(
                f"put every {target_color} object into the {container_shape}"
            )
        instruction = "; then ".join(instruction_parts) + "."
        task_id = f"attribute_grouped_{task_index:03d}"
        return {
            "task_id": task_id,
            "difficulty": "attribute_grouped",
            "instruction": instruction,
            "objects": all_objects,
            "initial_state": {"at": at, "holding": {}},
            "goal": {"facts": goal_facts},
            "solvable": solvable,
            "hard_factors": {
                "attribute_grouped": True,
                "attribute_groups": num_groups,
                "attribute_group_size": objects_per_group,
                "multi_distractors": num_distractors >= 2,
                "multi_step": num_goals >= 2,
                "dual_arm": bool(rules.get("dual_arm", False)) and num_goals >= 2,
            },
        }

    def _generate_exclusion_constraint_task(
        self,
        rules: dict[str, Any],
        task_index: int,
    ) -> dict[str, Any]:
        """Generate a feasible task with explicit do-not-move distractors."""
        num_goals = self._pick_range(rules["num_goal_objects"])
        num_containers = self._pick_range(rules["num_containers"])
        num_distractors = self._pick_range(rules["num_distractors"])

        shapes = rules.get("shapes", ["cube", "block", "wedge", "sphere"])
        goal_colors = set()
        movable_pool = [
            (color, shape)
            for color in COLOR_RGB
            for shape in shapes
        ]
        movable_descriptions = self.rng.sample(movable_pool, num_goals + num_distractors)
        goal_descriptions = movable_descriptions[:num_goals]
        goal_colors = {color for color, _ in goal_descriptions}

        constraint_color = self._pick(
            [color for color in COLOR_RGB if color not in goal_colors]
        )
        if num_distractors > len(shapes):
            raise RuntimeError("exclusion_constraint needs enough distinct shapes")
        distractor_descriptions = [
            (constraint_color, shape)
            for shape in self.rng.sample(shapes, num_distractors)
        ]

        container_shapes = rules.get("containers", ["tray", "box", "bowl"])
        container_pool = [
            (color, shape)
            for color in COLOR_RGB
            for shape in container_shapes
        ]
        container_descriptions = self.rng.sample(container_pool, num_containers)

        goal_objects = [self._fresh_name(color, shape) for color, shape in goal_descriptions]
        containers = [self._fresh_name(color, shape) for color, shape in container_descriptions]
        distractors = [self._fresh_name(color, shape) for color, shape in distractor_descriptions]
        all_objects = goal_objects + containers + distractors
        at = {obj: "table" for obj in all_objects}

        goal_facts = []
        for i, obj in enumerate(goal_objects):
            target = containers[i % len(containers)]
            goal_facts.append(f"on({obj}, {target})")
        # A preserved distractor is part of the goal: final on(d, table).
        # This keeps the constraint measurable under the existing goal checker.
        goal_facts.extend(f"on({obj}, table)" for obj in distractors)

        solvable = self._verify_solvability(
            all_objects, at, goal_facts, bool(rules.get("dual_arm", False))
        )
        target_phrase = ", ".join(
            f"the {color} {shape}" for color, shape in goal_descriptions
        )
        if rules.get("generic_constraint", False):
            instruction = (
                f"Put {target_phrase} into the {container_descriptions[0][1]}, "
                f"but do not move any {constraint_color} object."
            )
        else:
            constraint_phrase = ", ".join(
                f"the {color} {shape}" for color, shape in distractor_descriptions
            )
            instruction = (
                f"Put {target_phrase} into the {container_descriptions[0][1]}, "
                f"but do not move {constraint_phrase}."
            )
        task_id = f"exclusion_constraint_{task_index:03d}"
        return {
            "task_id": task_id,
            "difficulty": "exclusion_constraint",
            "instruction": instruction,
            "objects": all_objects,
            "initial_state": {"at": at, "holding": {}},
            "goal": {"facts": goal_facts},
            "solvable": solvable,
            "hard_factors": {
                "exclusion_constraint": True,
                "constraint_attribute": constraint_color,
                "generic_constraint": bool(rules.get("generic_constraint", False)),
                "preserved_distractors": num_distractors,
                "multi_distractors": num_distractors >= 2,
                "multi_step": num_goals >= 2,
                "dual_arm": bool(rules.get("dual_arm", False)) and num_goals >= 2,
            },
        }

    def _generate_logical_conflict_task(
        self,
        rules: dict[str, Any],
        task_index: int,
    ) -> dict[str, Any]:
        """Generate a deterministic logical-conflict infeasible task."""
        num_containers = max(2, self._pick_range(rules.get("num_containers", 2)))
        num_distractors = self._pick_range(rules.get("num_distractors", 0))
        shapes = rules.get("shapes", ["cube", "block", "wedge", "sphere"])
        movable_pool = [
            (color, shape)
            for color in COLOR_RGB
            for shape in shapes
        ]
        needed = 1 + num_distractors
        if needed > len(movable_pool):
            raise RuntimeError("Not enough unique descriptions for logical conflict scene")
        movable_descriptions = self.rng.sample(movable_pool, needed)
        goal_description = movable_descriptions[0]
        distractor_descriptions = movable_descriptions[1:]

        container_shapes = rules.get("containers", ["tray", "box", "bowl"])
        if num_containers > len(container_shapes):
            raise RuntimeError("logical_conflict needs two containers")
        container_shapes_selected = self.rng.sample(container_shapes, num_containers)
        colors = self.rng.sample(list(COLOR_RGB.keys()), num_containers)
        container_descriptions = list(zip(colors, container_shapes_selected))

        goal_color, goal_shape = goal_description
        goal_object = self._fresh_name(goal_color, goal_shape)
        containers = [self._fresh_name(color, shape) for color, shape in container_descriptions]
        distractors = [self._fresh_name(color, shape) for color, shape in distractor_descriptions]
        all_objects = [goal_object] + containers + distractors
        at = {obj: "table" for obj in all_objects}

        # Same visible object is required on two distinct non-table surfaces.
        # This is contradictory under the pick/place transition system.
        goal_facts = [
            f"on({goal_object}, {containers[0]})",
            f"on({goal_object}, {containers[1]})",
        ]
        instruction = (
            f"Put the {goal_color} {goal_shape} into both the "
            f"{container_shapes_selected[0]} and the {container_shapes_selected[1]}."
        )
        task_id = f"logical_conflict_{task_index:03d}"
        return {
            "task_id": task_id,
            "difficulty": "logical_conflict",
            "instruction": instruction,
            "objects": all_objects,
            "initial_state": {"at": at, "holding": {}},
            "goal": {"facts": goal_facts},
            "solvable": False,
            "hard_factors": {
                "logical_conflict": True,
                "conflicting_surfaces": [containers[0], containers[1]],
            },
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
        ambiguity_rate: float = 0.4,
    ) -> str:
        parts = []
        for i, fact in enumerate(goal_facts):
            inner = fact[3:-1]
            obj, tgt = [s.strip() for s in inner.split(",", 1)]
            name_parts = obj.rsplit("_", 2) if "_" in obj else [obj]
            color = name_parts[0]
            shape = name_parts[1] if len(name_parts) >= 3 else "cube"
            arm = None
            if dual_arm and i < len(goal_objects):
                arm = "left" if i % 2 == 0 else "right"
            if ambiguous and self.rng.random() < ambiguity_rate:
                parts.append(_ambiguous_phrase(color, shape, tgt))
            else:
                parts.append(_goal_phrase(color, shape, tgt, arm))
        return "; then ".join(parts).capitalize() + "."

    # -----------------------------------------------------------------------
    def generate_all(self) -> list[dict[str, Any]]:
        tasks = []
        idx = 0
        for diff in ["easy", "medium", "hard"]:
            if diff not in self.rules:
                continue
            rules = self.rules[diff]
            scene_rules = {**self.rules, **rules}
            for i in range(rules["count"]):
                t = self._generate_feasible_task(diff, scene_rules, idx)
                t["solvable"] = self._verify_solvability(
                    t["objects"], t["initial_state"]["at"], t["goal"]["facts"],
                    rules.get("dual_arm", False)
                )
                tasks.append(t)
                idx += 1
        for diff in ["attribute_grouped", "exclusion_constraint"]:
            if diff not in self.rules:
                continue
            rules = self.rules[diff]
            scene_rules = {**self.rules, **rules}
            for i in range(rules["count"]):
                if diff == "attribute_grouped":
                    t = self._generate_attribute_grouped_task(scene_rules, idx)
                else:
                    t = self._generate_exclusion_constraint_task(scene_rules, idx)
                if diff != "logical_conflict":
                    t["solvable"] = self._verify_solvability(
                        t["objects"], t["initial_state"]["at"], t["goal"]["facts"],
                        rules.get("dual_arm", False)
                    )
                tasks.append(t)
                idx += 1
        if "logical_conflict" in self.rules:
            rules = self.rules["logical_conflict"]
            for i in range(rules["count"]):
                t = self._generate_logical_conflict_task({**self.rules, **rules}, idx)
                tasks.append(t)
                idx += 1
        # Infeasible
        if "infeasible" in self.rules:
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
    pretty_stats = ", ".join(f"{name}: {count}" for name, count in stats.items())
    print(f"  {pretty_stats}")
    print(f"  solvable: {solvable_count}/{total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
