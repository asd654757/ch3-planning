"""Zero-VLM persistent scene/render/contact feasibility check."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

os.environ.setdefault("MUJOCO_GL", "egl")

from ch3.execution.multiobject_scene import build_scene_xml, make_scene


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    import mujoco
    import numpy as np
    from PIL import Image
    from metaworld.asset_path_utils import full_V3_path_for

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=False)
    public = output / "public"
    private = output / "evaluation_only"
    public.mkdir()
    private.mkdir()
    with tempfile.TemporaryDirectory(prefix="ch3-multiobject-") as temp:
        xml = Path(temp) / "scene.xml"
        build_scene_xml(Path(full_V3_path_for("sawyer_xyz/sawyer_pick_place_v3.xml")), xml)
        env = make_scene(xml, seed=args.seed)
        try:
            before = env.data.time
            for _ in range(30):
                env.step(np.array([0., 0., 0., -1.]))
            frame = np.flip(np.asarray(env.render()), (0, 1)).copy()
            if frame.shape != (480, 480, 3) or float(frame.std()) < 5:
                raise RuntimeError("invalid or blank camera frame")
            Image.fromarray(frame).save(public / "current.png")
            handles = {"obj": "objGeom", "candidate_blue": "candidate_blue_geom",
                       "candidate_yellow": "candidate_yellow_geom",
                       "placement_region": "placement_region_geom"}
            # Segmentation labels are evaluator truth, never model inputs.
            with mujoco.Renderer(env.model, height=480, width=480) as renderer:
                renderer.enable_segmentation_rendering()
                renderer.update_scene(env.data, camera="corner2")
                segmentation = np.flip(renderer.render(), (0, 1)).copy()
            np.save(private / "segmentation.npy", segmentation)
            details = {}
            for body, geom in handles.items():
                geom_id = env.model.geom(geom).id
                mask = ((segmentation[:, :, 0] == geom_id) &
                        (segmentation[:, :, 1] == int(mujoco.mjtObj.mjOBJ_GEOM)))
                y, x = np.nonzero(mask)
                details[body] = {"position": env.data.body(body).xpos.tolist(),
                                 "visible_pixels": int(mask.sum()),
                                 "bbox": [int(x.min()), int(y.min()), int(x.max()) + 1, int(y.max()) + 1] if len(x) else None}
            (private / "truth.json").write_text(json.dumps(details, indent=2), encoding="utf-8")
            visible = all(item["visible_pixels"] >= 20 for item in details.values())
            summary = {"record_type": "multiobject_scene_smoke", "seed": args.seed,
                       "model_calls": 0, "episode_resets": 1, "settle_steps": 30,
                       "simulation_time_advanced": float(env.data.time - before),
                       "contact_count": int(env.data.ncon), "all_entities_visible": visible,
                       "camera_orientation": "corner2_flip_both_axes",
                       "frame_std": float(frame.std()), "robot_task_success": None,
                       "scope": "scene/render feasibility only; binding and manipulation pending"}
            (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print(json.dumps(summary), flush=True)
            if not visible:
                raise RuntimeError("one or more scene entities are occluded")
        finally:
            env.close()


if __name__ == "__main__":
    main()
