"""Same-state multiview recovery evidence audit; no online policy comparison."""
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
VIEWS = ("corner2", "topview", "corner")


def view_evidence(initial_pixels, captures, observation_id):
    """Independent camera streams only; no condition or evaluator inputs."""
    from ch3.execution.observed_continuation import checked_place_continuation
    from ch3.execution.visual_holding import failed_pick_evidence, holding_evidence
    result = {}
    for camera in VIEWS:
        samples = [record[camera] for record in captures]
        targets, hands = [s["blue"] for s in samples], [s["hand"] for s in samples]
        holding = holding_evidence(initial_pixels[camera], targets, hands)
        negative = failed_pick_evidence(initial_pixels[camera], targets, hands)
        decision, _ = checked_place_continuation(holding,
            target_visible=samples[-1]["green"] is not None,
            observation_id=observation_id + ":" + camera)
        result[camera] = {"holding": holding, "nonfollowing": negative,
                          "continuation": decision}
    return result


def run_case(seed, screen, release, output):
    import numpy as np
    from PIL import Image
    from metaworld.asset_path_utils import full_V3_path_for
    from ch3.compiler.executable_plan import ExecutableStep
    from ch3.execution.fixed_pickplace import FixedPickPlaceController
    from ch3.execution.multiobject_scene import build_scene_xml, make_scene
    from ch3.execution.occlusion_fixture import add_observation_screen, render_fixed_view
    from ch3.execution.skill_contract import BackendContract
    from ch3.execution.visual_geometry import fixed_mujoco_calibration, project_point, visual_binding
    from ch3.execution.visual_holding import color_pixel

    output.mkdir(parents=True, exist_ok=False)
    result = {"seed": seed, "screen": screen, "controlled_release": release,
              "status": "error", "rounds": [], "api_calls": 0,
              "scope": "multiview_development_feasibility_not_policy_performance"}
    started = time.perf_counter()
    env = None
    steps = {}
    render_seconds = {c: 0. for c in VIEWS}
    stage = "setup"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            xml = Path(tmp) / "scene.xml"
            build_scene_xml(Path(full_V3_path_for("sawyer_xyz/sawyer_pick_place_v3.xml")),
                            xml, hide_goal_marker=True)
            if screen:
                add_observation_screen(xml)
            env = make_scene(xml, seed=seed)
            step = env.step

            def counted(action):
                steps[stage] = steps.get(stage, 0) + 1
                obs, reward, terminal, truncated, info = step(action)
                if terminal or truncated:
                    raise RuntimeError("episode ended in " + stage)
                return obs, reward, terminal, truncated, info

            env.step = counted
            for _ in range(30):
                env.step(np.array([0., 0., 0., -1.]))
            calibrations = {c: fixed_mujoco_calibration(env.model, env.data, camera=c,
                                                      width=480, height=480) for c in VIEWS}

            def capture(name):
                # Equality check is instrumentation only, never a policy feature.
                before = env.data.qpos.copy()
                views = {}
                for camera in VIEWS:
                    clock = time.perf_counter()
                    rgb = render_fixed_view(env, camera)
                    elapsed = time.perf_counter() - clock
                    render_seconds[camera] += elapsed
                    Image.fromarray(np.flip(rgb, (0, 1))).save(output / f"{name}_{camera}.png")

                    def pixel(color):
                        try:
                            return color_pixel(rgb, color)[0]
                        except ValueError:
                            return None

                    views[camera] = {"blue": pixel("blue"), "green": pixel("green"),
                        "hand": project_point(calibrations[camera], env.get_endeff_pos()),
                        "image_sha256": hashlib.sha256(rgb.tobytes()).hexdigest(),
                        "render_seconds": elapsed}
                if not np.array_equal(before, env.data.qpos):
                    raise RuntimeError("render changed physical state")
                return views

            initial = capture("initial")
            result["initial_views"] = initial
            # One fixed control camera in ALL conditions, chosen before results.
            camera = "corner"
            if initial[camera]["blue"] is None or initial[camera]["green"] is None:
                raise ValueError("fixed_initial_control_camera_missing_target")
            bindings = {obj: visual_binding(calibrations[camera], object_id=obj,
                observation_id="initial", pixel=initial[camera][color], plane_z=z)
                for obj, color, z in (("blue_candidate", "blue", .02), ("green_region", "green", .008))}
            controller = FixedPickPlaceController(env)
            contract = BackendContract(controller.backend, frozenset({"pick", "place"}),
                                       frozenset({"right"}), "mujoco_world")
            pick = ExecutableStep(1, "fixed_pick", "grasp",
                                  {"object_id": "blue_candidate", "arm": "right"}, "pick")
            stage = "initial_pick"
            result["pick_receipt"] = controller.execute(contract.request(pick,
                bindings=bindings, observation_id="initial"))
            # Evaluation snapshots do not enter view_evidence or any command.
            eval_pick_height = float(env.data.body("candidate_blue").xpos[2])
            grip = 1.
            if release:
                stage = "controlled_release"
                for _ in range(30):
                    env.step(np.array([0., 0., 0., -1.]))
                grip = -1.
            eval_after_release_height = float(env.data.body("candidate_blue").xpos[2])
            for round_index in range(2):
                stage = f"probe_{round_index}"
                origin = np.asarray(env.get_endeff_pos()).copy()
                captures = []
                for frame_index, shift in enumerate((0., .04 if round_index == 0 else -.04)):
                    target = origin + [shift, 0., 0.]
                    for _ in range(40):
                        env.step(np.r_[np.clip(10 * (target-env.get_endeff_pos()), -1, 1), grip])
                        if np.linalg.norm(env.get_endeff_pos()-target) < .005:
                            break
                    captures.append(capture(f"round{round_index}_frame{frame_index}"))
                result["rounds"].append({"index": round_index, "captures": captures,
                    "evidence": view_evidence({c: initial[c]["blue"] for c in VIEWS}, captures,
                                               f"round:{round_index}")})
            result["evaluation_only"] = {"pick_height_m": eval_pick_height,
                "after_release_height_m": eval_after_release_height,
                "release_realized": (eval_pick_height > .1 and eval_after_release_height < .06) if release else None,
                "task_success_measured": False}
            result["status"] = "complete_audit"
    except Exception as exc:
        result["error"] = {"stage": stage, "type": type(exc).__name__, "message": str(exc)}
    finally:
        if env is not None:
            result["episode_resets"] = int(env._ch3_reset_count)
            env.close()
        result.update(control_steps=steps, render_seconds=render_seconds,
                      total_wall_seconds=time.perf_counter()-started)
        (output / "summary.json").write_text(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seeds", type=int, nargs="+", default=[920, 921, 922])
    args = parser.parse_args()
    if len(set(args.seeds)) != len(args.seeds):
        parser.error("duplicate seeds")
    args.output.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).resolve()
    protocol = Path("docs/recovery_lookahead_strict_protocol_20261109.md")
    (args.output / "source_snapshot.py").write_bytes(source.read_bytes())
    (args.output / "protocol_snapshot.md").write_bytes(protocol.read_bytes())
    manifest = {"seeds": args.seeds, "screens": [False, True], "release": [False, True],
        "views": VIEWS, "initial_control_camera": "corner", "rounds": 2,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "protocol_sha256": hashlib.sha256(protocol.read_bytes()).hexdigest(),
        "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "api_calls": 0, "evidence_kind": "capability_audit_not_advantage_test"}
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    rows = []
    for seed in args.seeds:
        for screen in (False, True):
            for release in (False, True):
                row = run_case(seed, screen, release,
                               args.output / f"seed_{seed}_screen_{int(screen)}_release_{int(release)}")
                rows.append(row)
                (args.output / "audit.json").write_text(json.dumps({"manifest": manifest, "episodes": rows}, indent=2))
                print(json.dumps({k: row[k] for k in ("seed", "screen", "controlled_release", "status")}
                    | {"holding_by_round": [{c: r["evidence"][c]["holding"]["status"] for c in VIEWS}
                                             for r in row["rounds"]], "error": row.get("error")}), flush=True)
    if any(r["status"] != "complete_audit" for r in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
