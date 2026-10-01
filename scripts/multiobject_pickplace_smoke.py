"""RGB-only color-target waypoint smoke; not language grounding benchmark."""
import argparse
import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("MUJOCO_GL", "egl")


def color_pixel(frame, color):
    """Restricted color heuristic: no segmentation labels or object handles."""
    import numpy as np
    rgb = frame.astype(float)
    if color == "blue":
        mask = (rgb[:, :, 2] > 75) & (rgb[:, :, 2] > 1.6 * rgb[:, :, 0]) & (rgb[:, :, 2] > 1.4 * rgb[:, :, 1])
    elif color == "green":
        mask = (rgb[:, :, 1] > 75) & (rgb[:, :, 1] > 1.6 * rgb[:, :, 0]) & (rgb[:, :, 1] > 1.4 * rgb[:, :, 2])
    else:
        raise ValueError("unsupported color")
    y, x = np.nonzero(mask)
    if len(x) < 20:
        raise ValueError("insufficient target color evidence")
    return (float(np.median(x)), float(np.median(y))), int(len(x))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    import numpy as np
    from PIL import Image
    from metaworld.asset_path_utils import full_V3_path_for
    from ch3.execution.multiobject_scene import build_scene_xml, make_scene
    from ch3.execution.visual_geometry import fixed_mujoco_calibration, visual_binding
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
            pick_result = controller.execute(contract.request(pick, bindings=bindings, observation_id="initial"))
            Image.fromarray(np.flip(np.asarray(env.render()), (0, 1))).save(output / "after_pick.png")
            final_positions = {name: env.data.body(name).xpos.copy() for name in initial_positions}
            lift_height = float(final_positions["candidate_blue"][2] - initial_positions["candidate_blue"][2])
            lifted = lift_height > .06
            report = {"record_type": "multiobject_rgb_pick_smoke", "model_calls": 0, "episode_resets": 1,
                      "seed": 0, "camera": "corner2", "pixel_frame": "native_render",
                      "assumed_plane_z": {"blue_candidate": .02, "green_region": .008},
                      "target_selection": "fixed blue color heuristic, not language model",
                      "blue_pixel": blue, "green_pixel": green, "color_pixels": [blue_count, green_count],
                      "estimated_blue_position": bindings["blue_candidate"].position,
                      "pick_execution": pick_result, "evaluation_only": {
                          "blue_lifted": lifted, "blue_lift_height_m": lift_height,
                          "lift_threshold_m": .06,
                          "body_displacement_m": {name: float(np.linalg.norm(final_positions[name] - initial_positions[name]))
                                                  for name in initial_positions}},
                      "place_attempted": False, "task_success": False,
                      "scope": "pick feasibility only; no holding confirmation or recovery loop"}
            (output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report), flush=True)
        finally:
            env.close()


if __name__ == "__main__":
    main()
