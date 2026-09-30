"""Same-state multiview acquisition, not manipulation or recovery evidence."""

import argparse
import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("MUJOCO_GL", "egl")

from ch3.execution.multiobject_scene import build_scene_xml, make_scene


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    import mujoco
    import numpy as np
    from PIL import Image, ImageDraw
    from metaworld.asset_path_utils import full_V3_path_for

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=False)
    public, private = output / "public", output / "evaluation_only"
    public.mkdir()
    private.mkdir()
    with tempfile.TemporaryDirectory() as temp:
        xml = Path(temp) / "scene.xml"
        build_scene_xml(Path(full_V3_path_for("sawyer_xyz/sawyer_pick_place_v3.xml")),
                        xml, hide_goal_marker=True)
        env = make_scene(xml, seed=args.seed)
        try:
            for _ in range(30):
                env.step(np.array([0., 0., 0., -1.]))
            # Camera specification is fixed, not chosen using evaluator labels.
            views = ["corner2", "topview", "gripper_view"]
            frozen = env.data.qpos.copy()
            timestamp = float(env.data.time)
            sheet = Image.new("RGB", (480 * len(views), 504), "white")
            draw = ImageDraw.Draw(sheet)
            metadata = []
            with mujoco.Renderer(env.model, height=480, width=480) as renderer:
                for index, camera in enumerate(views):
                    render_camera = camera
                    if camera == "gripper_view":
                        render_camera = mujoco.MjvCamera()
                        render_camera.type = mujoco.mjtCamera.mjCAMERA_FREE
                        render_camera.lookat[:] = [0., 0.6, 0.2]
                        render_camera.distance = 0.65
                        render_camera.azimuth = 135
                        render_camera.elevation = -25
                    renderer.update_scene(env.data, camera=render_camera)
                    frame = np.flip(renderer.render(), (0, 1)).copy()
                    if float(frame.std()) < 5:
                        raise RuntimeError(f"blank view: {camera}")
                    Image.fromarray(frame).save(public / f"{camera}.png")
                    sheet.paste(Image.fromarray(frame), (480 * index, 24))
                    draw.text((480 * index + 8, 5), camera, fill="black")
                    metadata.append({"camera": camera, "simulation_time": timestamp,
                                     "orientation": "flip_both_axes", "frame_std": float(frame.std())})
                    renderer.enable_segmentation_rendering()
                    renderer.update_scene(env.data, camera=render_camera)
                    segmentation = np.flip(renderer.render(), (0, 1)).copy()
                    np.save(private / f"{camera}_segmentation.npy", segmentation)
                    truth = {}
                    for body, geom in {"obj": "objGeom", "candidate_blue": "candidate_blue_geom",
                                       "candidate_yellow": "candidate_yellow_geom",
                                       "placement_region": "placement_region_geom"}.items():
                        mask = ((segmentation[:, :, 0] == env.model.geom(geom).id) &
                                (segmentation[:, :, 1] == int(mujoco.mjtObj.mjOBJ_GEOM)))
                        y, x = np.nonzero(mask)
                        truth[body] = {"visible_pixels": int(mask.sum()),
                                       "bbox": [int(x.min()), int(y.min()), int(x.max()) + 1,
                                                int(y.max()) + 1] if len(x) else None}
                    (private / f"{camera}_truth.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")
                    renderer.disable_segmentation_rendering()
            if not np.array_equal(frozen, env.data.qpos) or timestamp != float(env.data.time):
                raise RuntimeError("state changed while acquiring views")
            sheet.save(public / "multiview.png")
            (public / "manifest.json").write_text(json.dumps([{
                "case_id": "multiobject_multiview_feasibility_000",
                "instruction": "Put the red cylinder onto the green rectangular platform.",
                "image_path": "corner2.png", "seed": args.seed}], indent=2), encoding="utf-8")
            summary = {"record_type": "multiobject_multiview_smoke", "episode_resets": 1,
                       "settle_steps": 30, "views": metadata, "model_calls": 0,
                       "goal_marker_hidden": True, "same_state_verified": True,
                       "gripper_camera": {"lookat": [0., 0.6, 0.2], "distance": 0.65,
                                          "azimuth": 135, "elevation": -25},
                       "execution_attempted": False,
                       "scope": "multiview acquisition only; hand visibility requires inspection"}
            (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print(json.dumps(summary), flush=True)
        finally:
            env.close()


if __name__ == "__main__":
    main()
