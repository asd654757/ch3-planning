"""Static camera geometry smoke; no model calls or manipulation."""
import argparse
import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("MUJOCO_GL", "egl")

from ch3.execution.multiobject_scene import build_scene_xml, make_scene
from ch3.execution.visual_geometry import fixed_mujoco_calibration, pixel_to_plane, project_point


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    import numpy as np
    import mujoco
    from metaworld.asset_path_utils import full_V3_path_for
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory() as temp:
        xml = Path(temp) / "scene.xml"
        build_scene_xml(Path(full_V3_path_for("sawyer_xyz/sawyer_pick_place_v3.xml")), xml,
                        hide_goal_marker=True)
        env = make_scene(xml, seed=0)
        try:
            calibration = fixed_mujoco_calibration(env.model, env.data, camera="corner2", width=480, height=480)
            public = {"camera": "corner2", "coordinate_frame": calibration.coordinate_frame,
                      "intrinsics": calibration.intrinsics.tolist(),
                      "camera_to_execution": calibration.camera_to_execution.tolist(),
                      "width": 480, "height": 480,
                      "display_transform": "flip_both_axes", "plane_z_assumption": 0.0}
            (output / "calibration.json").write_text(json.dumps(public, indent=2), encoding="utf-8")
            errors = []
            for x in [-.12, 0, .12]:
                for y in [.6, .7, .8]:
                    # Synthetic grid, not object pose truth or online targets.
                    point = (x, y, 0.)
                    pixel = project_point(calibration, point)
                    reconstructed = pixel_to_plane(calibration, pixel=pixel, plane_z=0.)
                    errors.append(float(np.linalg.norm(np.array(point) - reconstructed)))
            report = {"record_type": "visual_calibration_smoke", "synthetic_grid_points": len(errors),
                      "max_roundtrip_error_m": max(errors), "model_calls": 0,
                      "execution_attempted": False,
                      "scope": "algebraic roundtrip only; rendered pixel alignment not yet independently verified"}
            # Independent evaluator-side check against actual rendered geometry.
            # These positions/labels never enter public calibration or targeting.
            checks = []
            with mujoco.Renderer(env.model, height=480, width=480) as renderer:
                renderer.enable_segmentation_rendering()
                renderer.update_scene(env.data, camera="corner2")
                segmentation = renderer.render().copy()
            for name in ["objGeom", "candidate_blue_geom", "candidate_yellow_geom", "placement_region_geom"]:
                geom_id = env.model.geom(name).id
                mask = ((segmentation[:, :, 0] == geom_id) &
                        (segmentation[:, :, 1] == int(mujoco.mjtObj.mjOBJ_GEOM)))
                y, x = np.nonzero(mask)
                pixel = project_point(calibration, env.data.geom_xpos[geom_id])
                inside = bool(len(x) and x.min() <= pixel[0] <= x.max() and y.min() <= pixel[1] <= y.max())
                checks.append({"geom": name, "projected_pixel": pixel, "center_inside_rendered_bbox": inside})
            private = output / "evaluation_only"
            private.mkdir()
            (private / "projection_checks.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
            report["rendered_center_checks_passed"] = sum(item["center_inside_rendered_bbox"] for item in checks)
            report["scope"] = "roundtrip and rendered bbox alignment; not exact subpixel calibration or manipulation"
            (output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report), flush=True)
            if max(errors) > 1e-8:
                raise RuntimeError("calibration roundtrip failed")
            if not all(item["center_inside_rendered_bbox"] for item in checks):
                raise RuntimeError("rendered projection alignment failed")
        finally:
            env.close()


if __name__ == "__main__":
    main()
