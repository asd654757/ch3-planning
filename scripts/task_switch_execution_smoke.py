"""Bounded persistent task-switch pilot. RGB targeting; truth is scoring only.

Initial empty/table and untouched-yellow/table are controlled preconditions.
No general open-world or empty-hand estimator is claimed. Initial goal is a
scripted external command; initial and remaining plans may use the live model.
"""
import argparse
from dataclasses import asdict, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile

os.environ.setdefault("MUJOCO_GL", "egl")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--setting", choices=["current_state_replan", "rule_recovery"], default="current_state_replan")
    parser.add_argument("--blackout-release", action="store_true", help="negative control; no yellow pick permitted")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    out = args.output_dir
    import numpy as np
    from PIL import Image
    from metaworld.asset_path_utils import full_V3_path_for
    from ch3.execution.multiobject_scene import build_scene_xml, make_scene
    from ch3.execution.fixed_pickplace import FixedPickPlaceController
    from ch3.execution.skill_contract import BackendContract
    from ch3.execution.visual_geometry import fixed_mujoco_calibration, visual_binding, visual_region_binding, project_point
    from ch3.execution.visual_holding import color_pixel, holding_evidence
    from ch3.execution.visual_release import release_evidence
    from ch3.execution.recovery_context import RecoveryContext, recovery_route
    from ch3.vlm.task_switch_recovery import generate_switch_plan, evaluate_switch_plan, switch_fixture
    from ch3.vlm.persistent_scene_bridge import GoalSelection, generate_initial
    from ch3.vlm.client import DashScopeVLMClient
    from ch3.state.world_state import WorldState

    report = {"record_type": "persistent_task_switch_execution_pilot", "seed": args.seed,
              "setting": args.setting, "scope": "restricted_known_color_controlled_scene_not_open_world",
              "instruction_source": "scripted_external_command",
              "initial_state_source": "controlled_empty_hand_and_objects_on_table_fixture",
              "untouched_yellow_state_source": "controlled_fixture_not_full_visual_state_estimation",
              "task_success": False, "episode_resets": 0, "recovery_episode_resets": 0,
              "model_calls": 0, "events": [], "yellow_pick_attempted": False,
              "blackout_release": args.blackout_release,
              "geometry_assumptions": {"table_cube_centroid_z": .02, "region_cube_centroid_z": .028,
                  "region_surface_z": .008, "region_pixel_statistic": "RGB_ray_plane_world_extrema_center_1percent_trim",
                  "limitation": "known_flat_regions_edges_assumed_visible; not_general_occlusion_reconstruction"}}
    context = None
    env = None
    stage = "scene_setup"
    with tempfile.TemporaryDirectory() as temp:
        try:
            xml = Path(temp) / "scene.xml"
            build_scene_xml(Path(full_V3_path_for("sawyer_xyz/sawyer_pick_place_v3.xml")), xml,
                            hide_goal_marker=True, add_return_region=True)
            env = make_scene(xml, seed=args.seed)
            # Four skills plus feedback probes require a different horizon
            # than the native single-pair 500-step task. Fixed for ALL settings.
            env.max_path_length = 800
            report["episode_step_limit"] = 800
            report["episode_resets"] = 1
            for _ in range(30):
                env.step(np.array([0., 0., 0., -1.]))
            calibration = fixed_mujoco_calibration(env.model, env.data, camera="corner2", width=480, height=480)
            controller = FixedPickPlaceController(env)
            contract = BackendContract(controller.backend, frozenset({"pick", "place"}), frozenset({"right"}), "mujoco_world")
            client = DashScopeVLMClient(env_path=args.env_file, timeout=60, max_retries=0)

            def frame(name, blackout=False):
                rgb = np.asarray(env.render()).copy()
                if blackout:
                    rgb[:] = 0
                path = out / (name + ".png")
                Image.fromarray(np.flip(rgb, (0, 1))).save(path)
                print(f"[task-switch] seed={args.seed} stage={stage} frame={name}", flush=True)
                return rgb, path

            def locate(rgb, color):
                return color_pixel(rgb, color, require_unique=True, pixel_statistic="bbox_center" if color in {"magenta", "green"} else "median")[0]

            def binding(rgb, obj, color, observation_id, height):
                if color in {"green", "magenta"}:
                    return visual_region_binding(calibration, frame=rgb, color=color,
                        object_id=obj, observation_id=observation_id, plane_z=height)
                return visual_binding(calibration, object_id=obj, observation_id=observation_id,
                                      pixel=locate(rgb, color), plane_z=height)

            def probe(name, color, grip):
                origin = np.asarray(env.get_endeff_pos()).copy()
                records = []
                deltas = ([0., 0., .04], [.04, 0., .04]) if grip < 0 else ([0., 0., 0.], [.04, 0., 0.])
                for i, delta in enumerate(deltas):
                    target = origin + delta
                    for _ in range(40):
                        _, _, terminated, truncated, _ = env.step(np.r_[np.clip(10 * (target - env.get_endeff_pos()), -1, 1), grip])
                        if terminated or truncated:
                            raise ValueError("episode ended during probe")
                        if np.linalg.norm(env.get_endeff_pos() - target) < .005:
                            break
                    rgb, path = frame(f"{name}_{i}", blackout=args.blackout_release and name == "blue_release")
                    try:
                        pixel = locate(rgb, color)
                    except ValueError:
                        pixel = None
                    records.append({"target_pixel": pixel, "hand_pixel": project_point(calibration, env.get_endeff_pos()),
                                    "image": str(path)})
                return records, rgb, path

            rgb, initial_image = frame("before")
            initial_blue = locate(rgb, "blue")
            locate(rgb, "yellow")
            locate(rgb, "magenta")
            initial_bindings = {"blue_candidate": binding(rgb, "blue_candidate", "blue", "initial", .02),
                                "green_region": binding(rgb, "green_region", "green", "initial", .008)}
            stage = "initial_planning"
            report["model_calls"] += 1
            initial_plan = generate_initial(client, goal=GoalSelection(object_id="blue_candidate", target_id="green_region",
                needs_observation=False, unsupported_constraints=[]), image_path=initial_image,
                log_path=out / "initial_plan_call.json", initial_state=WorldState.table_scene({"blue_candidate", "green_region"}))
            stage = "blue_pick"
            result = controller.execute(contract.request(initial_plan.steps[0], bindings=initial_bindings, observation_id="initial"))
            report["events"].append({"stage": stage, "execution": result})
            if not result["completed"]:
                raise ValueError("blue pick controller incomplete")
            records, rgb, holding_image = probe("blue_holding", "blue", 1.)
            held = holding_evidence(initial_blue, [r["target_pixel"] for r in records], [r["hand_pixel"] for r in records])
            report["blue_holding_evidence"] = dict(held, records=records)
            if held["status"] != "holding_supported":
                raise ValueError("blue holding unsupported; no switch execution")
            stage = "task_switch_planning"
            state = switch_fixture()
            context = RecoveryContext("Transport the blue cube to the green region.", {"on(blue_candidate, green_region)"})
            context.observe(facts=state.facts(), image_sha256=sha256(holding_image.read_bytes()).hexdigest(),
                            evidence_source="RGB_supported_blue_holding_plus_untouched_yellow_table_fixture")
            context.record_execution(event_id="blue_pick", skill="pick", object_id="blue_candidate",
                                     outcome="supported_success", evidence_source="RGB_displacement_and_hand_comotion")
            context.update_task("Cancel the unexecuted blue-to-green placement. Now transport the yellow cube to the green region, returning the held blue cube to table.",
                {"on(yellow_candidate, green_region)", "on(blue_candidate, table)", "hand_empty(right)"})
            report["switch_route"] = recovery_route(execution_status="supported_success", next_prerequisites="supported", intent="misaligned")
            if args.setting == "current_state_replan":
                report["model_calls"] += 1
                plan = generate_switch_plan(client, image_path=holding_image, log_path=out / "switch_plan_call.json", state=state, context=context)
            else:
                # Explicit rule baseline has the SAME current state/new task.
                raw = {"actions": [dict(step_id=1, skill="place", object_id="blue_candidate", target_id="table", arm="right"),
                    dict(step_id=2, skill="pick", object_id="yellow_candidate", arm="right"),
                    dict(step_id=3, skill="place", object_id="yellow_candidate", target_id="green_region", arm="right")]}
                audit, plan = evaluate_switch_plan(raw, state, state_source=context.evidence_source)
                (out / "rule_plan_validation.json").write_text(json.dumps(audit, indent=2))
                if plan is None:
                    raise ValueError("rule remaining plan rejected")
            report["remaining_plan"] = plan.to_list()
            version = context.task_version
            stage = "blue_return"
            # `table` is explicitly mapped to a visible magenta subregion.
            observation_id = "blue_return"
            return_binding = binding(rgb, "table", "magenta", observation_id, .008)
            report["table_binding"] = asdict(return_binding)
            fresh = {"blue_candidate": replace(initial_bindings["blue_candidate"], position=tuple(env.get_endeff_pos()), observation_id=observation_id), "table": return_binding}
            if version != context.task_version:
                raise ValueError("task changed before dispatch")
            result = controller.execute(contract.request(plan.steps[0], bindings=fresh, observation_id=observation_id))
            report["events"].append({"stage": stage, "execution": result})
            if not result["completed"]:
                raise ValueError("blue release controller incomplete")
            records, rgb, released_image = probe("blue_release", "blue", -1.)
            try:
                current = binding(rgb, "blue_candidate", "blue", "released", .028)
                destination = binding(rgb, "table", "magenta", "released", .008)
                released = release_evidence([r["target_pixel"] for r in records], [r["hand_pixel"] for r in records],
                    current.position[:2], destination.position[:2], prior_holding_supported=True,
                    open_retreat_completed=result["completed"], single_object_fixture=True)
            except ValueError:
                released = {"status": "unknown", "empty_hand_supported": False, "reason": "missing_or_ambiguous_release_RGB"}
            report["blue_release_evidence"] = dict(released, records=records)
            if released["status"] != "release_supported":
                context.observe(facts=[], image_sha256=sha256(released_image.read_bytes()).hexdigest(),
                                evidence_source="release_RGB_insufficient", status="unknown")
                raise ValueError("release not supported; yellow pick refused")
            context.observe(facts={"on(blue_candidate, table)", "on(yellow_candidate, table)", "on(green_region, table)", "hand_empty(right)"},
                image_sha256=sha256(released_image.read_bytes()).hexdigest(), evidence_source="restricted_RGB_release_plus_untouched_yellow_fixture")
            context.record_execution(event_id="blue_return", skill="place", object_id="blue_candidate",
                                     outcome="supported_success", evidence_source="restricted_RGB_release_evidence")
            stage = "yellow_pick"
            from ch3.schema.model_plan import ModelPlan
            from ch3.validator.pipeline import Validator
            from ch3.execution.observed_continuation import fixed_registry
            # Revalidate planned suffix from newly supported state, never the
            # predicted blue release state. Local numbering is reset for validation.
            raw = json.loads((out / ("switch_plan_validation.json" if args.setting == "current_state_replan" else "rule_plan_validation.json")).read_text())["plan"]
            suffix_raw = {"actions": [dict(a, step_id=i + 1) for i, a in enumerate(raw["actions"][1:])]}
            post_release_state = WorldState.table_scene(state.objects)
            validation = Validator(state.objects, fixed_registry()).validate(ModelPlan.model_validate(suffix_raw), post_release_state)
            if not validation.valid or "on(yellow_candidate, green_region)" not in validation.final_state.facts():
                raise ValueError("suffix rejected from observed post-release state")
            yellow_initial = locate(rgb, "yellow")
            yellow_binding = binding(rgb, "yellow_candidate", "yellow", "yellow_pick", .02)
            if version != context.task_version:
                raise ValueError("task changed before yellow dispatch")
            report["yellow_pick_attempted"] = True
            result = controller.execute(contract.request(plan.steps[1], bindings={"yellow_candidate": yellow_binding}, observation_id="yellow_pick"))
            report["events"].append({"stage": stage, "execution": result})
            if not result["completed"]:
                raise ValueError("yellow pick controller incomplete")
            records, rgb, path = probe("yellow_holding", "yellow", 1.)
            held = holding_evidence(yellow_initial, [r["target_pixel"] for r in records], [r["hand_pixel"] for r in records])
            report["yellow_holding_evidence"] = dict(held, records=records)
            if held["status"] != "holding_supported":
                raise ValueError("yellow holding unsupported")
            # Blue may be occluded while yellow is held. Do not copy a
            # historical success into current facts, and do not halt an unrelated
            # safe placement whose own prerequisites are supported. Recheck blue
            # at the final observation; its goal remains pending until then.
            context.observe(facts={"holding(right, yellow_candidate)", "on(green_region, table)"},
                            image_sha256=sha256(path.read_bytes()).hexdigest(), evidence_source="RGB_yellow_comotion_and_visible_green_only")
            context.record_execution(event_id="yellow_pick", skill="pick", object_id="yellow_candidate",
                                     outcome="supported_success", evidence_source="RGB_displacement_and_hand_comotion")
            stage = "yellow_place"
            observation_id = "yellow_place"
            fresh = {"yellow_candidate": replace(yellow_binding, position=tuple(env.get_endeff_pos()), observation_id=observation_id),
                     "green_region": binding(rgb, "green_region", "green", observation_id, .008)}
            if version != context.task_version:
                raise ValueError("task changed before placement")
            result = controller.execute(contract.request(plan.steps[2], bindings=fresh, observation_id=observation_id))
            report["events"].append({"stage": stage, "execution": result})
            if not result["completed"]:
                raise ValueError("yellow place controller incomplete")
            records, rgb, path = probe("yellow_release", "yellow", -1.)
            try:
                y = binding(rgb, "yellow_candidate", "yellow", "final", .028)
                g = binding(rgb, "green_region", "green", "final", .008)
                released = release_evidence([r["target_pixel"] for r in records], [r["hand_pixel"] for r in records],
                    y.position[:2], g.position[:2], prior_holding_supported=True, open_retreat_completed=True, single_object_fixture=True,
                    destination_tolerance=(.04, .025))
            except ValueError:
                released = {"status": "unknown", "empty_hand_supported": False}
            report["yellow_release_evidence"] = dict(released, records=records)
            if released["status"] != "release_supported":
                raise ValueError("final release evidence insufficient")
            context.record_execution(event_id="yellow_place", skill="place", object_id="yellow_candidate",
                                     outcome="supported_success", evidence_source="restricted_RGB_release_evidence")
            # Recheck blue rather than promote its historical success to current goal.
            b = binding(rgb, "blue_candidate", "blue", "final", .028)
            t = binding(rgb, "table", "magenta", "final", .008)
            if not np.all(np.abs(np.asarray(b.position[:2]) - t.position[:2]) <= [.025, .018]):
                raise ValueError("blue return effect no longer supported")
            context.observe(facts={"on(yellow_candidate, green_region)", "on(blue_candidate, table)", "hand_empty(right)"},
                image_sha256=sha256(path.read_bytes()).hexdigest(), evidence_source="restricted_RGB_final_locations_and_release")
            report["online_goal_supported"] = not context.remaining_goals
            stage = "completed"
        except Exception as exc:
            report["error"] = str(exc)
        finally:
            report["stage"] = stage
            if context is not None:
                report.update(task_version=context.task_version, observation_version=context.observation_version,
                    executed_history=[asdict(e) for e in context.history], remaining_goal_facts=sorted(context.remaining_goals))
            if env is not None:
                # Independent truth evaluator ONLY, after all online decisions.
                yellow = env.data.body("candidate_yellow").xpos.copy()
                blue = env.data.body("candidate_blue").xpos.copy()
                green = env.data.body("placement_region").xpos.copy()
                table = env.data.body("return_region").xpos.copy()
                yellow_ok = bool(np.all(np.abs(yellow[:2] - green[:2]) <= [.052, .027]) and .01 <= yellow[2] <= .055)
                blue_ok = bool(np.all(np.abs(blue[:2] - table[:2]) <= [.032, .017]) and .01 <= blue[2] <= .055)
                report["control_steps"] = int(env.curr_path_length)
                report["evaluation_only"] = {"yellow_in_green": yellow_ok, "blue_in_return_region": blue_ok,
                    "source": "simulator_truth_not_online_feedback", "blue_xyz": blue.tolist(), "yellow_xyz": yellow.tolist()}
                report["task_success"] = bool(stage == "completed" and yellow_ok and blue_ok)
                env.close()
            (out / "summary.json").write_text(json.dumps(report, indent=2))
            print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
