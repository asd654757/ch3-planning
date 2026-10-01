"""RGB-only color-target waypoint smoke; not language grounding benchmark."""
import argparse
from dataclasses import replace
import json
import os
import tempfile
from pathlib import Path

from ch3.execution.visual_holding import color_pixel, holding_evidence
from ch3.execution.observed_continuation import checked_place_continuation

os.environ.setdefault("MUJOCO_GL", "egl")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--visual-follow", action="store_true", help="probe co-motion; place only with RGB evidence")
    parser.add_argument("--skip-pick", action="store_true", help="ungrasped negative control")
    parser.add_argument("--pick-max-steps", type=int, default=260)
    parser.add_argument("--occlude-feedback", action="store_true", help="controlled full-frame observation blackout")
    parser.add_argument("--reobserve-rounds", type=int, default=0, help="bounded extra observation rounds")
    parser.add_argument("--blackout-rounds", type=int, default=0, help="first N observation rounds receive blackout")
    parser.add_argument("--language-instruction", help="enable restricted VLM goal resolution and remaining-plan generation")
    parser.add_argument("--env-file")
    parser.add_argument("--repair-remainder", action="store_true", help="one existing R1_FROM_STATE fallback after rejection")
    parser.add_argument("--inject-unknown-target", action="store_true", help="controlled remainder fault, never natural model error")
    args = parser.parse_args()
    if not 0 <= args.reobserve_rounds <= 3 or args.blackout_rounds < 0:
        parser.error("reobserve-rounds must be 0..3 and blackout-rounds nonnegative")
    import numpy as np
    from PIL import Image
    from metaworld.asset_path_utils import full_V3_path_for
    from ch3.execution.multiobject_scene import build_scene_xml, make_scene
    from ch3.execution.visual_geometry import fixed_mujoco_calibration, visual_binding, project_point
    from ch3.execution.skill_contract import BackendContract
    from ch3.execution.fixed_pickplace import FixedPickPlaceController
    from ch3.compiler.executable_plan import ExecutableStep

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=False)
    language_goal = None
    language_error = None
    model_calls = 0
    with tempfile.TemporaryDirectory() as temp:
        xml = Path(temp) / "scene.xml"
        build_scene_xml(Path(full_V3_path_for("sawyer_xyz/sawyer_pick_place_v3.xml")), xml, hide_goal_marker=True)
        env = make_scene(xml, seed=0)
        try:
            for _ in range(30):
                env.step(np.array([0., 0., 0., -1.]))
            frame = np.asarray(env.render()).copy()  # native, not flipped
            Image.fromarray(np.flip(frame, (0, 1))).save(output / "before.png")
            calibration = fixed_mujoco_calibration(env.model, env.data, camera="corner2", width=480, height=480)
            blue, blue_count = color_pixel(frame, "blue")
            green, green_count = color_pixel(frame, "green")
            if args.language_instruction:
                from ch3.vlm.client import DashScopeVLMClient
                from ch3.vlm.persistent_scene_bridge import select_goal, generate_remaining
                client = DashScopeVLMClient(env_path=args.env_file, timeout=60, max_retries=0)
                model_calls += 1
                try:
                    language_goal = select_goal(client, instruction=args.language_instruction,
                        image_path=output / "before.png", log_path=output / "language_goal_call.json")
                except Exception as exc:
                    (output / "summary.json").write_text(json.dumps({"execution_attempted": False,
                        "model_calls": model_calls, "error": str(exc), "stage": "language_goal"}, indent=2))
                    print("[language-scene] goal rejected; no execution", flush=True)
                    return
            # Explicit fixed geometry assumptions, not observed body heights.
            bindings = {"blue_candidate": visual_binding(calibration, object_id="blue_candidate", observation_id="initial",
                                                       pixel=blue, plane_z=.02),
                        "green_region": visual_binding(calibration, object_id="green_region", observation_id="initial",
                                                       pixel=green, plane_z=.008)}
            contract = BackendContract(FixedPickPlaceController.backend, frozenset({"pick", "place"}),
                                       frozenset({"right"}), "mujoco_world")
            controller = FixedPickPlaceController(env)
            pick = ExecutableStep(1, "fixed_pick", "grasp", {"object_id": "blue_candidate", "arm": "right"}, "pick")
            # Evaluator snapshots kept outside controller, never used for targeting.
            initial_positions = {name: env.data.body(name).xpos.copy() for name in ["obj", "candidate_blue", "candidate_yellow"]}
            pick_result = ({"completed": False, "reason": "skipped_negative_control", "steps": 0}
                           if args.skip_pick else controller.execute(contract.request(pick, bindings=bindings, observation_id="initial"),
                                                                    max_steps=args.pick_max_steps))
            Image.fromarray(np.flip(np.asarray(env.render()), (0, 1))).save(output / "after_pick.png")
            # Scoring snapshot only: never used to gate probing or placement.
            pick_lift_height = float(env.data.body("candidate_blue").xpos[2] - initial_positions["candidate_blue"][2])
            holding = {"status": "unknown", "reason": "not_tested"}
            place_result = None
            probe_steps = 0
            evidence_frames = []
            observation_history = []
            continuation = {"action": "observe_again", "reason": "not_tested"}
            if args.visual_follow:
                for round_index in range(args.reobserve_rounds + 1):
                    # Only proprioception and rendered RGB drive this branch.
                    evidence_frames = []
                    probe_origin = np.asarray(env.get_endeff_pos()).copy()
                    for index, delta in enumerate(([0., 0., 0.], [.04 if round_index % 2 == 0 else -.04, 0., 0.])):
                        waypoint = probe_origin + delta
                        for _ in range(40):
                            action = np.r_[np.clip(10 * (waypoint - env.get_endeff_pos()), -1, 1),
                                           -1 if args.skip_pick else 1]
                            _, _, terminated, truncated, _ = env.step(action)
                            probe_steps += 1
                            if terminated or truncated:
                                raise RuntimeError("episode ended during visual probe")
                            if np.linalg.norm(env.get_endeff_pos() - waypoint) < .005:
                                break
                        current = np.asarray(env.render()).copy()
                        Image.fromarray(np.flip(current, (0, 1))).save(output / f"probe_round{round_index}_{index}.png")
                        feedback_frame = np.zeros_like(current) if (args.occlude_feedback or round_index < args.blackout_rounds) else current
                        Image.fromarray(np.flip(feedback_frame, (0, 1))).save(output / f"feedback_round{round_index}_{index}.png")
                        try:
                            target_pixel = color_pixel(feedback_frame, "blue")[0]
                        except ValueError:
                            target_pixel = None
                        evidence_frames.append({"target_pixel": target_pixel,
                                                "hand_pixel": project_point(calibration, env.get_endeff_pos())})
                    holding = holding_evidence(blue, [f["target_pixel"] for f in evidence_frames],
                                               [f["hand_pixel"] for f in evidence_frames])
                    try:
                        fresh_green = color_pixel(feedback_frame, "green")[0]
                    except ValueError:
                        fresh_green = None
                    observation_id = f"after_probe_{round_index}"
                    continuation, place = checked_place_continuation(holding,
                        target_visible=fresh_green is not None, observation_id=observation_id)
                    observation_history.append({"round": round_index, "evidence_frames": evidence_frames,
                                                "holding": holding, "decision": continuation})
                    if place is not None:
                        break
                if place is None:
                    continuation = dict(continuation, action="stop", reason="observation_budget_exhausted",
                                        last_observation_reason=continuation["reason"])
                if place is not None:
                    if language_goal is not None:
                        model_calls += 1
                        try:
                            place = generate_remaining(client, evidence=holding, goal=language_goal,
                                image_path=output / f"feedback_round{round_index}_1.png",
                                log_path=output / "remaining_plan_call.json",
                                repair_on_rejection=args.repair_remainder,
                                inject_unknown_target=args.inject_unknown_target)
                            continuation = dict(continuation, plan_source="vlm_observed_state_remainder")
                        except Exception as exc:
                            language_error = str(exc)
                            place = None
                            continuation = dict(continuation, action="stop", reason="model_remainder_rejected")
                        audit_file = output / "remaining_plan_validation.json"
                        if audit_file.exists():
                            audit = json.loads(audit_file.read_text())
                            model_calls += int(audit.get("repair_attempted", False))
                            continuation = dict(continuation, repair_audit=audit)
                if place is not None:
                    # Refresh destination from current RGB; never from scoring truth.
                    fresh_bindings = {
                        # Holding evidence plus proprioception gives an approximate
                        # held-object reference, not a measured object 6D pose.
                        "blue_candidate": replace(bindings["blue_candidate"],
                            position=tuple(float(x) for x in env.get_endeff_pos()),
                            observation_id=observation_id),
                        "green_region": visual_binding(calibration, object_id="green_region",
                            observation_id=observation_id, pixel=fresh_green, plane_z=.008)}
                    place_result = controller.execute(contract.request(place, bindings=fresh_bindings, observation_id=observation_id))
                    for _ in range(30):
                        env.step(np.array([0., 0., 0., -1.]))
                    Image.fromarray(np.flip(np.asarray(env.render()), (0, 1))).save(output / "after_place.png")
            final_positions = {name: env.data.body(name).xpos.copy() for name in initial_positions}
            lift_height = float(final_positions["candidate_blue"][2] - initial_positions["candidate_blue"][2])
            lifted = pick_lift_height > .06
            region = env.data.body("placement_region").xpos.copy()
            placed = bool(place_result is not None and
                          # Entire cube footprint inside the known region extent.
                          np.all(np.abs(final_positions["candidate_blue"][:2] - region[:2]) <= [.052, .027]) and
                          .01 <= final_positions["candidate_blue"][2] <= .055)
            report = {"record_type": "multiobject_rgb_pick_smoke", "model_calls": model_calls, "episode_resets": 1,
                      "instruction": args.language_instruction, "language_error": language_error,
                      "language_goal": language_goal.model_dump() if language_goal else None,
                      "seed": 0, "camera": "corner2", "pixel_frame": "native_render",
                      "assumed_plane_z": {"blue_candidate": .02, "green_region": .008},
                      "target_selection": "fixed blue color heuristic, not language model",
                      "blue_pixel": blue, "green_pixel": green, "color_pixels": [blue_count, green_count],
                      "estimated_blue_position": bindings["blue_candidate"].position,
                      "pick_execution": pick_result, "evaluation_only": {
                          "blue_lifted_after_pick": lifted, "blue_lift_height_after_pick_m": pick_lift_height,
                          "blue_final_height_change_m": lift_height,
                          "blue_in_region_after_release": placed,
                          "placement_rule": "cube footprint inside region, center z in [0.01,0.055] after opening and settling",
                          "lift_threshold_m": .06,
                          "body_displacement_m": {name: float(np.linalg.norm(final_positions[name] - initial_positions[name]))
                                                  for name in initial_positions}},
                      "visual_holding": holding, "evidence_frames": evidence_frames,
                      "probe_steps": probe_steps, "place_execution": place_result,
                      "place_attempted": place_result is not None, "task_success": placed,
                      "negative_control": args.skip_pick,
                      "pick_max_steps": args.pick_max_steps, "controlled_feedback_blackout": args.occlude_feedback,
                      "continuation_decision": continuation, "observation_history": observation_history,
                      "reobserve_rounds_budget": args.reobserve_rounds, "blackout_rounds": args.blackout_rounds,
                      "pick_attempts": 0 if args.skip_pick else 1,
                      "scope": ("restricted language goal and VLM remaining-plan generation with optional R1_FROM_STATE fallback; initial pick scripted, no full ROUTED"
                                if language_goal else "restricted RGB feedback and local remaining-plan validation; no language planner or recovery loop")}
            (output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report), flush=True)
        finally:
            env.close()


if __name__ == "__main__":
    main()
