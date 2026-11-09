"""Zero-API, same-episode recovery-option feasibility audit, not a policy trial.

Fixed conditions are development diagnostics. No belief probabilities are fitted,
and the two-step recovery planner is NOT used to control these episodes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

os.environ.setdefault("MUJOCO_GL", "egl")

CONDITIONS = ("normal", "temporary_feedback_loss", "persistent_feedback_loss",
              "precontact_timeout", "post_pick_release")


def public_options(*, holding, target_visible, initial_pixel, fresh_pixel,
                   pick_result, precontact_probe_preserved, observations_left, observation_id):
    """Only RGB evidence, command trace and fixed-fixture assumptions enter.

    Non-following does not imply an empty hand. No perturbation name, seed,
    simulator object state, reward, score or model value is accepted here.
    """
    from ch3.execution.early_pick_recovery import early_recovery_gate
    from ch3.execution.observed_continuation import checked_place_continuation
    decision, _ = checked_place_continuation(holding, target_visible=target_visible,
                                              observation_id=observation_id)
    retry = early_recovery_gate(pick_result, initial_empty_fixture=True,
                               initial_pixel=initial_pixel, fresh_pixel=fresh_pixel)
    retry_authorized = bool(retry["authorized"] and precontact_probe_preserved)
    allowed = ["stop"]
    if observations_left > 0:
        allowed.append("observe")
    if decision["action"] == "execute_remaining":
        allowed.append("continue")
    if retry_authorized:
        allowed.append("retry")
    return {"allowed": allowed, "continuation_guard": decision,
            "retry_guard": retry, "retry_authorized": retry_authorized,
            "precontact_probe_preserved": bool(precontact_probe_preserved),
            "replan_available": False,
            "scope": "known_fixture_options_not_general_empty_hand_estimation"}


def run_case(seed: int, condition: str, output: Path):
    import numpy as np
    from PIL import Image
    from metaworld.asset_path_utils import full_V3_path_for
    from ch3.compiler.executable_plan import ExecutableStep
    from ch3.execution.fixed_pickplace import FixedPickPlaceController
    from ch3.execution.multiobject_scene import build_scene_xml, make_scene
    from ch3.execution.observed_continuation import checked_place_continuation
    from ch3.execution.skill_contract import BackendContract
    from ch3.execution.visual_geometry import fixed_mujoco_calibration, project_point, visual_binding
    from ch3.execution.visual_holding import color_pixel, failed_pick_evidence, holding_evidence

    if condition not in CONDITIONS:
        raise ValueError("unknown diagnostic condition")
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    costs, timings, observations, commands = {}, {}, [], []
    stage = "setup"
    env = None
    summary = {"seed": seed, "condition": condition, "status": "error",
               "api_calls": 0, "planner_used": False, "observations": observations,
               "commands": commands, "control_steps": costs, "wall_seconds": timings,
               "condition_is_evaluator_metadata_not_policy_input": True}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            xml = Path(tmp) / "scene.xml"
            build_scene_xml(Path(full_V3_path_for("sawyer_xyz/sawyer_pick_place_v3.xml")),
                            xml, hide_goal_marker=True)
            env = make_scene(xml, seed=seed)
            original_step = env.step

            def counted_step(action):
                costs[stage] = costs.get(stage, 0) + 1
                return original_step(action)

            env.step = counted_step
            for _ in range(30):
                env.step(np.array([0., 0., 0., -1.]))
            calibration = fixed_mujoco_calibration(env.model, env.data, camera="corner2",
                                                   width=480, height=480)
            initial_frame = np.asarray(env.render()).copy()
            Image.fromarray(np.flip(initial_frame, (0, 1))).save(output / "initial.png")
            initial_blue = color_pixel(initial_frame, "blue")[0]
            initial_green = color_pixel(initial_frame, "green")[0]
            controller = FixedPickPlaceController(env)
            contract = BackendContract(controller.backend, frozenset({"pick", "place"}),
                                       frozenset({"right"}), "mujoco_world")
            pick = ExecutableStep(1, "fixed_pick", "grasp",
                                  {"object_id": "blue_candidate", "arm": "right"}, "pick")

            def bindings(obs_id, blue, green):
                return {"blue_candidate": visual_binding(calibration, object_id="blue_candidate",
                    observation_id=obs_id, pixel=blue, plane_z=.02),
                    "green_region": visual_binding(calibration, object_id="green_region",
                    observation_id=obs_id, pixel=green, plane_z=.008)}

            stage = "initial_pick"
            clock = time.perf_counter()
            pick_result = controller.execute(contract.request(pick,
                bindings=bindings("initial", initial_blue, initial_green), observation_id="initial"),
                max_steps=20 if condition == "precontact_timeout" else 260)
            timings[stage] = time.perf_counter() - clock
            summary["initial_pick_receipt"] = pick_result
            # Evaluation-only snapshots; never passed to the public gate.
            evaluation_pick_height = float(env.data.body("candidate_blue").xpos[2])
            evaluation_release_height = None
            commands.append({"command": "pick", "receipt": pick_result})
            active = [e for e in pick_result.get("trace", []) if e.get("steps", 0)]
            precontact = bool(active and all(e["phase"] == 0 for e in active)
                              and pick_result.get("reason") == "waypoint_timeout")
            grip = -1. if precontact else 1.
            if condition == "post_pick_release":
                stage = "controlled_release"
                clock = time.perf_counter()
                for _ in range(30):
                    _, _, terminated, truncated, _ = env.step(np.array([0., 0., 0., -1.]))
                    if terminated or truncated:
                        raise RuntimeError("episode ended during controlled release")
                grip = -1.
                commands.append({"command": "open_and_settle", "steps": 30,
                                 "source": "controlled_disturbance_visible_in_command_history"})
                timings[stage] = time.perf_counter() - clock
                evaluation_release_height = float(env.data.body("candidate_blue").xpos[2])

            def probe(index, blackout=False):
                nonlocal stage
                stage = f"probe_{index}"
                clock = time.perf_counter()
                origin = np.asarray(env.get_endeff_pos()).copy()
                frames = []
                clearance_preserved = True
                for frame_index, delta in enumerate((0., .04 if index % 2 == 0 else -.04)):
                    waypoint = origin + [delta, 0., 0.]
                    for _ in range(40):
                        hand = np.asarray(env.get_endeff_pos())
                        clearance_preserved &= bool(grip == -1 and hand[2] >= .12)
                        action = np.r_[np.clip(10 * (waypoint - hand), -1, 1), grip]
                        _, _, terminated, truncated, _ = env.step(action)
                        if terminated or truncated:
                            raise RuntimeError("episode ended during probe")
                        if np.linalg.norm(env.get_endeff_pos() - waypoint) < .005:
                            break
                    raw = np.asarray(env.render()).copy()
                    rgb = np.zeros_like(raw) if blackout else raw
                    Image.fromarray(np.flip(raw, (0, 1))).save(output / f"raw_{index}_{frame_index}.png")
                    Image.fromarray(np.flip(rgb, (0, 1))).save(output / f"feedback_{index}_{frame_index}.png")

                    def pixel(color):
                        try:
                            return color_pixel(rgb, color)[0]
                        except ValueError:
                            return None

                    frames.append({"target_pixel": pixel("blue"), "destination_pixel": pixel("green"),
                                   "hand_pixel": project_point(calibration, env.get_endeff_pos()),
                                   "feedback_sha256": hashlib.sha256(rgb.tobytes()).hexdigest()})
                holding = holding_evidence(initial_blue, [f["target_pixel"] for f in frames],
                                           [f["hand_pixel"] for f in frames])
                nonfollowing = failed_pick_evidence(initial_blue, [f["target_pixel"] for f in frames],
                                                    [f["hand_pixel"] for f in frames])
                public = {"observation_id": f"probe:{index}", "frames": frames,
                          "holding": holding, "nonfollowing": nonfollowing}
                public["options"] = public_options(holding=holding,
                    target_visible=frames[-1]["destination_pixel"] is not None,
                    initial_pixel=initial_blue, fresh_pixel=frames[-1]["target_pixel"],
                    pick_result=pick_result, precontact_probe_preserved=precontact and clearance_preserved,
                    observations_left=max(0, 1-index), observation_id=public["observation_id"])
                observations.append(public)
                timings[stage] = time.perf_counter() - clock
                return public

            # Fixed diagnostic schedule. No evaluator truth chooses a recovery.
            obs = probe(0, blackout=condition != "normal")
            if "continue" not in obs["options"]["allowed"] and "retry" not in obs["options"]["allowed"]:
                obs = probe(1, blackout=condition == "persistent_feedback_loss")
            selected = "continue" if "continue" in obs["options"]["allowed"] else "stop"
            if "retry" in obs["options"]["allowed"]:
                selected = "retry"
                stage = "retry_retract"
                clock = time.perf_counter()
                reset_result = controller.retract_open()
                timings[stage] = time.perf_counter() - clock
                commands.append({"command": "retract_open_not_episode_reset", "receipt": reset_result})
                if reset_result["completed"]:
                    rgb = np.asarray(env.render()).copy()
                    Image.fromarray(np.flip(rgb, (0, 1))).save(output / "retry_authorization.png")
                    try:
                        fresh_blue, fresh_green = color_pixel(rgb, "blue")[0], color_pixel(rgb, "green")[0]
                    except ValueError:
                        fresh_blue = fresh_green = None
                    gate = public_options(holding={"status": "unknown"}, target_visible=fresh_green is not None,
                        initial_pixel=initial_blue, fresh_pixel=fresh_blue, pick_result=pick_result,
                        precontact_probe_preserved=bool(float(env.get_endeff_pos()[2]) >= .12),
                        observations_left=0, observation_id="post_retract")
                    summary["post_retract_gate"] = gate
                    if gate["retry_authorized"] and fresh_green is not None:
                        stage = "retry_pick"
                        clock = time.perf_counter()
                        retry_result = controller.execute(contract.request(pick,
                            bindings=bindings("retry", fresh_blue, fresh_green), observation_id="retry"))
                        timings[stage] = time.perf_counter() - clock
                        commands.append({"command": "retry_pick", "receipt": retry_result})
                        # Receipt remains distinct from holding evidence.
                        grip = 1.
                        precontact = False
                        obs = probe(2)
            summary["first_recovery_action_after_acquisition"] = selected
            selected = "stop"
            if "continue" in obs["options"]["allowed"]:
                selected = "continue"
                decision, place = checked_place_continuation(obs["holding"],
                    target_visible=True, observation_id=obs["observation_id"])
                # Geometry is based on proprioception and fresh RGB only.
                from dataclasses import replace
                fresh_bindings = bindings(obs["observation_id"], initial_blue,
                                           obs["frames"][-1]["destination_pixel"])
                fresh_bindings["blue_candidate"] = replace(fresh_bindings["blue_candidate"],
                    position=tuple(float(x) for x in env.get_endeff_pos()))
                stage = "place"
                clock = time.perf_counter()
                result = controller.execute(contract.request(place, bindings=fresh_bindings,
                                                              observation_id=obs["observation_id"]))
                timings[stage] = time.perf_counter() - clock
                commands.append({"command": "place", "receipt": result, "guard": decision})
                grip = -1.
                stage = "post_place_settle"
                for _ in range(30):
                    env.step(np.array([0., 0., 0., -1.]))
            summary["final_dispatch_decision"] = selected
            summary["episode_resets_measured"] = int(env._ch3_reset_count)
            summary["status"] = "complete_audit"

            # No observer, guard, planner or recovery callback after this point.
            stage = "terminal_scoring"
            clock = time.perf_counter()
            positions = []
            in_goal = []
            for _ in range(10):
                _, _, terminated, truncated, _ = env.step(np.array([0., 0., 0., grip]))
                pos = env.data.body("candidate_blue").xpos.copy()
                region = env.data.body("placement_region").xpos.copy()
                positions.append(pos)
                in_goal.append(bool(np.all(np.abs(pos[:2] - region[:2]) <= [.052, .027])
                                    and .01 <= pos[2] <= .055 and grip == -1.))
                if terminated or truncated:
                    break
            stable = bool(len(positions) == 10 and all(in_goal)
                          and max(float(np.linalg.norm(b-a)) for a, b in zip(positions, positions[1:])) < .001)
            timings[stage] = time.perf_counter() - clock
            summary["evaluation_only"] = {"task_success": stable,
                "blue_final_position": positions[-1].tolist(),
                "score_kind": "independent_geometry_10_step_stability_plus_commanded_open_not_contact_sensor",
                "scoring_truth_used_by_guard": False,
                "initial_pick_height_m": evaluation_pick_height,
                "post_release_height_m": evaluation_release_height,
                "release_perturbation_realized": (bool(evaluation_pick_height > .1 and evaluation_release_height < .06)
                    if condition == "post_pick_release" else None)}
    except Exception as exc:
        summary["error"] = {"stage": stage, "type": type(exc).__name__, "message": str(exc)}
    finally:
        if env is not None:
            summary["episode_resets_measured"] = int(env._ch3_reset_count)
            env.close()
        timings["total_including_setup_rendering_close"] = time.perf_counter() - started
        summary["total_explicit_control_steps"] = sum(costs.values())
        summary["cost_scope"] = "all explicit env.step calls after make_scene; native reset integration excluded"
        (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def summarize(rows):
    compact = []
    for row in rows:
        compact.append({"seed": row["seed"], "condition": row["condition"], "status": row["status"],
            "holding_sequence": [o["holding"]["status"] for o in row["observations"]],
            "allowed_sequence": [o["options"]["allowed"] for o in row["observations"]],
            "commands": [c["command"] for c in row["commands"]],
            "task_success": row.get("evaluation_only", {}).get("task_success", False),
            "control_steps": row["total_explicit_control_steps"],
            "wall_seconds": row["wall_seconds"]["total_including_setup_rendering_close"],
            "episode_resets": row.get("episode_resets_measured"), "error": row.get("error")})
    return {"evidence_kind": "physical_development_feasibility_not_policy_comparison",
            "episodes": compact, "api_calls": sum(r["api_calls"] for r in rows),
            "all_cases_retained": True, "probabilities_fitted": False,
            "limitations": [
                "Artificial full-frame dropout, fixed color/geometry assumptions; not natural occlusion or open world.",
                "Common unknown holding label does not mean identical full histories: command traces differ.",
                "A fixed rule executes all recovery choices here; no superiority of lookahead is measured.",
                "No replan macro or calibrated action model; cannot start a fair recovery-policy benchmark yet.",
                "Non-following is not an empty-hand certificate. Post-contact retry remains unavailable."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seeds", nargs="+", type=int, default=[910, 911])
    args = parser.parse_args()
    if len(set(args.seeds)) != len(args.seeds):
        parser.error("duplicate seed")
    args.output.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).resolve()
    manifest = {"seeds": args.seeds, "conditions": CONDITIONS, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "purpose": "development_feasibility_only", "no_model_calls": True}
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    rows = []
    for seed in args.seeds:
        for condition in CONDITIONS:
            row = run_case(seed, condition, args.output / f"seed_{seed}_{condition}")
            rows.append(row)
            report = summarize(rows)
            (args.output / "audit.json").write_text(json.dumps(report, indent=2))
            print(json.dumps(report["episodes"][-1]), flush=True)
    if any(r["status"] != "complete_audit" for r in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
