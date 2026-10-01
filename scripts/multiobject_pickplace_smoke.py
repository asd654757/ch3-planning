"""RGB-only color-target waypoint smoke; not language grounding benchmark."""
import argparse
from dataclasses import replace
import json
import os
import tempfile
from pathlib import Path

from ch3.execution.visual_holding import color_pixel, holding_evidence

os.environ.setdefault("MUJOCO_GL", "egl")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--visual-follow", action="store_true", help="probe co-motion; place only with RGB evidence")
    parser.add_argument("--skip-pick", action="store_true", help="ungrasped negative control")
    args = parser.parse_args()
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
                           if args.skip_pick else controller.execute(contract.request(pick, bindings=bindings, observation_id="initial")))
            Image.fromarray(np.flip(np.asarray(env.render()), (0, 1))).save(output / "after_pick.png")
            # Scoring snapshot only: never used to gate probing or placement.
            pick_lift_height = float(env.data.body("candidate_blue").xpos[2] - initial_positions["candidate_blue"][2])
            holding = {"status": "unknown", "reason": "not_tested"}
            place_result = None
            probe_steps = 0
            evidence_frames = []
            if args.visual_follow:
                # Only proprioception and rendered RGB drive this branch.
                probe_origin = np.asarray(env.get_endeff_pos()).copy()
                for index, delta in enumerate(([0., 0., 0.], [.04, 0., .02])):
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
                    Image.fromarray(np.flip(current, (0, 1))).save(output / f"probe_{index}.png")
                    try:
                        target_pixel = color_pixel(current, "blue")[0]
                    except ValueError:
                        target_pixel = None
                    evidence_frames.append({"target_pixel": target_pixel,
                                            "hand_pixel": project_point(calibration, env.get_endeff_pos())})
                holding = holding_evidence(blue, [f["target_pixel"] for f in evidence_frames],
                                           [f["hand_pixel"] for f in evidence_frames])
                if holding["status"] == "holding_supported":
                    # Refresh destination from current RGB; never from scoring truth.
                    fresh_green = color_pixel(current, "green")[0]
                    observation_id = "after_probe"
                    fresh_bindings = {
                        # Holding evidence plus proprioception gives an approximate
                        # held-object reference, not a measured object 6D pose.
                        "blue_candidate": replace(bindings["blue_candidate"],
                            position=tuple(float(x) for x in env.get_endeff_pos()),
                            observation_id=observation_id),
                        "green_region": visual_binding(calibration, object_id="green_region",
                            observation_id=observation_id, pixel=fresh_green, plane_z=.008)}
                    place = ExecutableStep(2, "fixed_place", "place",
                        {"object_id": "blue_candidate", "target_id": "green_region", "arm": "right"}, "place")
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
            report = {"record_type": "multiobject_rgb_pick_smoke", "model_calls": 0, "episode_resets": 1,
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
                      "scope": "restricted RGB execution feedback; no language planner or recovery loop"}
            (output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report), flush=True)
        finally:
            env.close()


if __name__ == "__main__":
    main()
