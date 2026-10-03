"""Two sequential RGB-targeted transfers in ONE episode, without model calls.

Backend feasibility gate only. Simulator body poses are terminal scoring inputs,
never controller bindings or online authorization. Known colors/planes required.
"""
import argparse
import json
import os
from pathlib import Path
import tempfile

os.environ.setdefault('MUJOCO_GL', 'egl')


def run(seed, out, *, reobserve_budget=0, model_planner=None, execution_recovery=False, pick_contact_offset=.015,
        feedback_v2=False, initial_y_spread=.025):
    import numpy as np
    from PIL import Image
    from metaworld.asset_path_utils import full_V3_path_for
    from ch3.execution.multiobject_scene import build_scene_xml, make_scene
    from ch3.execution.fixed_pickplace import FixedPickPlaceController
    from ch3.execution.skill_contract import BackendContract
    from ch3.execution.visual_geometry import fixed_mujoco_calibration, visual_binding, visual_region_binding, project_point
    from ch3.execution.visual_holding import color_pixel, holding_evidence
    from ch3.execution.visual_release import release_evidence
    from ch3.compiler.executable_plan import ExecutableStep

    out.mkdir(parents=True, exist_ok=False)
    report = dict(seed=seed, scope='known_color_reference_backend_feasibility_not_model_recovery',
                  episode_resets=0, model_calls=0, success=False, events=[],
                  truth_usage='scoring_only_not_targeting_or_authorization',
                  reference_sequence=['blue_to_return_region', 'yellow_to_green_region'],
                  protected_displacement_limit_m=.015, episode_step_limit=1000,
                  reobserve_budget=reobserve_budget, destination_observations=[],
                  pick_contact_offset_m=pick_contact_offset, feedback_v2=feedback_v2,
                  initial_y_spread_m=initial_y_spread)
    env = None
    stage = 'setup'
    with tempfile.TemporaryDirectory() as tmp:
        try:
            xml = Path(tmp) / 'scene.xml'
            build_scene_xml(Path(full_V3_path_for('sawyer_xyz/sawyer_pick_place_v3.xml')), xml,
                            hide_goal_marker=True, add_return_region=True)
            env = make_scene(xml, seed=seed, initial_y_spread=initial_y_spread)
            report['episode_resets'] = 1
            long_task = bool(model_planner is not None and model_planner.long_task)
            env.max_path_length = 1500 if long_task else 1000
            report['episode_step_limit'] = env.max_path_length
            for _ in range(30):
                env.step(np.array([0., 0., 0., -1.]))
            calibration = fixed_mujoco_calibration(env.model, env.data, camera='corner2', width=480, height=480)
            controller = FixedPickPlaceController(env, pick_contact_offset=pick_contact_offset)
            contract = BackendContract(controller.backend, frozenset({'pick', 'place'}), frozenset({'right'}), 'mujoco_world')

            def frame(name):
                rgb = np.asarray(env.render()).copy()
                Image.fromarray(np.flip(rgb, (0, 1))).save(out / f'{name}.png')
                return rgb

            def pixel(rgb, color):
                return color_pixel(rgb, color, require_unique=True)[0]

            def probe(name, color, grip):
                origin = np.asarray(env.get_endeff_pos()).copy()
                records = []
                offsets = ([0., 0., 0.], [.04, 0., 0.]) if grip > 0 else ([0., 0., .04], [.04, 0., .04])
                for i, delta in enumerate(offsets):
                    target = origin + delta
                    for _ in range(40):
                        _, _, terminated, truncated, _ = env.step(np.r_[np.clip(10 * (target - env.get_endeff_pos()), -1, 1), grip])
                        if terminated or truncated:
                            raise ValueError('episode_ended_during_probe')
                        if np.linalg.norm(env.get_endeff_pos() - target) < .005:
                            break
                    rgb = frame(f'{name}_{i}')
                    try:
                        p = pixel(rgb, color)
                    except ValueError:
                        p = None
                    records.append(dict(target_pixel=p, hand_pixel=project_point(calibration, env.get_endeff_pos())))
                return records, rgb

            # Static fixture assumption: reference captured BEFORE object/hand occlusion.
            # This is image calibration, not a simulator target coordinate lookup.
            references = {}
            if feedback_v2:
                reference_frame = frame('initial_region_references')
                for region_id, region_color in [('green_region', 'green'), ('return_region', 'magenta')]:
                    references[region_id] = visual_region_binding(calibration, frame=reference_frame,
                        color=region_color, object_id=region_id, observation_id='initial_region_references', plane_z=.008)
                report['region_references'] = {k: list(v.position) for k,v in references.items()}

            def check_observed_goal(records, region_id):
                from ch3.execution.observed_goal import observed_goal
                positions = []
                for record in records:
                    if record['target_pixel'] is None:
                        return dict(status='unknown', reason='post_release_object_not_visible')
                    position = visual_binding(calibration, object_id='observed_object', observation_id='post_release',
                        pixel=record['target_pixel'], plane_z=.028).position
                    positions.append(position[:2])
                return observed_goal(positions, references[region_id].position[:2])

            executable = None
            if model_planner is not None:
                stage = 'model_initial_planning'
                frame('model_initial')
                executable = model_planner.initial(out / 'model_initial.png')
                report['scope'] = 'live_model_planning_persistent_execution_restricted_fixture'
                report['initial_model_plan'] = executable.to_list()
            blue_after_first = None
            protected_distances = []  # evaluator-only snapshots, never gates
            sequence = [
                ('blue_candidate', 'blue', 'return_region', 'magenta'),
                ('yellow_candidate', 'yellow', 'green_region', 'green')]
            if long_task:
                sequence.insert(0, ('blue_candidate', 'blue', 'green_region', 'green'))
            report['reference_sequence'] = [f'{o}_to_{r}' for o, _, r, _ in sequence]
            source_heights = {}
            for n, (obj, color, region, region_color) in enumerate(sequence):
                stage = f'{color}_pick' if not long_task else f'transfer_{n}_{color}_pick'
                print(f'[reference-sequence] seed={seed} stage={stage}', flush=True)
                rgb = frame(stage)
                initial = pixel(rgb, color)
                oid = stage
                source = visual_binding(calibration, object_id=obj, observation_id=oid, pixel=initial,
                                        plane_z=source_heights.get(obj, .02))
                step = ExecutableStep(2*n+1, 'fixed_pick', 'grasp', dict(object_id=obj, arm='right'), 'pick')
                if executable is not None:
                    step = executable.steps[2*n]
                result = controller.execute(contract.request(step, bindings={obj: source}, observation_id=oid))
                report['events'].append(dict(stage=stage, execution=result))
                recovered_holding = False
                if not result['completed']:
                    frame(stage+'_failure')
                    if not (execution_recovery and model_planner is not None and model_planner.recovery):
                        raise ValueError('pick_waypoint_incomplete')
                    from ch3.execution.pick_failure_evidence import failure_scope
                    scope = failure_scope(result)
                    event = dict(error_code='PICK_WAYPOINT_TIMEOUT', primitive_trace=result, trace_scope=scope)
                    if scope == 'closure_attempted':
                        records, rgb = probe(stage+'_failure_holding', color, 1.)
                        held = holding_evidence(initial, [r['target_pixel'] for r in records], [r['hand_pixel'] for r in records])
                        if held['status'] != 'holding_supported':
                            raise ValueError('timeout_holding_unknown_no_replan')
                        event.update(state_source='RGB_positive_holding_comotion_after_timeout_plus_prior_fixture', holding_evidence=held)
                        name = stage+'_failure_holding_1'
                        repaired = model_planner.execution_repair(executable.steps[2*n+1:], out/f'{name}.png',
                            completed=n, holding_object=obj, event=event)
                        executable.steps[2*n+1:] = repaired.steps
                        recovered_holding = True
                    elif scope == 'open_only':
                        # Gripper never closed; prior empty state is preserved by trace.
                        # This is controlled trace inference, NOT general visual empty-hand sensing.
                        origin = np.asarray(env.get_endeff_pos()).copy()
                        target = origin.copy(); target[2] = .24
                        reached = False
                        for _ in range(60):
                            _, _, terminated, truncated, _ = env.step(np.r_[np.clip(10*(target-env.get_endeff_pos()), -1, 1), -1.])
                            if terminated or truncated:
                                raise ValueError('episode_ended_during_open_observe')
                            if np.linalg.norm(env.get_endeff_pos()-target)<.01:
                                reached=True; break
                        if not reached:
                            raise ValueError('open_observe_retreat_timeout')
                        records, rgb = probe(stage+'_failure_stationary', color, -1.)
                        pixels = [r['target_pixel'] for r in records]
                        if any(p is None for p in pixels) or np.linalg.norm(np.asarray(pixels[1])-pixels[0])>3:
                            raise ValueError('failed_pick_object_location_unknown')
                        fresh_source = visual_binding(calibration, object_id=obj, observation_id=stage+'_retry',
                            pixel=pixel(rgb, color), plane_z=source_heights.get(obj,.02))
                        # Support region must be visible and object remain at it.
                        support = next((r for o,_,r,_ in sequence[:n][::-1] if o==obj), 'table')
                        if support == 'table':
                            raise ValueError('table_failure_location_outside_recovery_scope')
                        support_color = 'green' if support=='green_region' else 'magenta'
                        region_binding = visual_region_binding(calibration, frame=rgb, color=support_color,
                            object_id=support, observation_id=stage+'_retry', plane_z=.008)
                        if not np.all(np.abs(np.asarray(fresh_source.position[:2])-region_binding.position[:2])<=[.025,.018]):
                            raise ValueError('failed_pick_object_not_at_supported_surface')
                        event.update(state_source='prior_RGB_release_plus_never_closed_gripper_trace_and_fresh_stationary_RGB_surface_location',
                                     supported_surface=support)
                        repaired = model_planner.execution_repair(executable.steps[2*n:], out/f'{stage}_failure_stationary_1.png',
                            completed=n, event=event)
                        executable.steps[2*n:] = repaired.steps
                        result = controller.execute(contract.request(executable.steps[2*n], bindings={obj:fresh_source},
                            observation_id=stage+'_retry'))
                        report['events'].append(dict(stage=stage+'_model_repaired_retry', execution=result, recovery_event=event))
                        if not result['completed']:
                            raise ValueError('model_repaired_pick_retry_incomplete')
                        initial = pixel(rgb, color)
                    else:
                        raise ValueError('pick_failure_trace_unknown')
                if not recovered_holding:
                    records, rgb = probe(stage+'_holding', color, 1.)
                    held = holding_evidence(initial, [r['target_pixel'] for r in records], [r['hand_pixel'] for r in records])
                report['events'][-1]['holding'] = held
                if held['status'] != 'holding_supported':
                    raise ValueError('holding_not_supported')
                if blue_after_first is not None:
                    protected_distances.append(float(np.linalg.norm(env.data.body('candidate_blue').xpos - blue_after_first)))
                stage = f'{color}_place' if not long_task else f'transfer_{n}_{color}_place'
                print(f'[reference-sequence] seed={seed} stage={stage}', flush=True)
                dest = None
                for observation_round in range(reobserve_budget + 1):
                    if observation_round:
                        # Closed gripper, bounded lift, no reset or release.
                        target = np.asarray(env.get_endeff_pos()).copy() + [0., 0., .035]
                        if target[2] > .30:
                            raise ValueError('reobserve_target_outside_workspace')
                        reached = False
                        for _ in range(40):
                            _, _, terminated, truncated, _ = env.step(np.r_[np.clip(10 * (target - env.get_endeff_pos()), -1, 1), 1.])
                            if terminated or truncated:
                                raise ValueError('episode_ended_during_reobserve')
                            if np.linalg.norm(env.get_endeff_pos() - target) < .005:
                                reached = True
                                break
                        if not reached:
                            raise ValueError('reobserve_lift_timeout')
                        records, rgb = probe(f'{stage}_reobserve_{observation_round}', color, 1.)
                        held = holding_evidence(initial, [r['target_pixel'] for r in records], [r['hand_pixel'] for r in records])
                        report['events'].append(dict(stage=stage+'_reobserve', round=observation_round, holding=held))
                        if held['status'] != 'holding_supported':
                            raise ValueError('holding_lost_during_reobserve')
                    oid = f'{stage}_observation_{observation_round}'
                    rgb = frame(oid)
                    try:
                        dest = visual_region_binding(calibration, frame=rgb, color=region_color,
                            object_id=region, observation_id=oid, plane_z=.008)
                        report['destination_observations'].append(dict(stage=stage, round=observation_round, supported=True,
                            fresh_estimate=list(dest.position)))
                        if feedback_v2:
                            from dataclasses import replace
                            # Do not overwrite a static pre-contact reference with an occluded center.
                            dest = replace(references[region], observation_id=oid)
                        break
                    except ValueError as exc:
                        report['destination_observations'].append(dict(stage=stage, round=observation_round, supported=False, reason=str(exc)))
                if dest is None:
                    raise ValueError('destination_observation_budget_exhausted')
                # Object binding is required by dispatch but place targets only the region.
                from dataclasses import replace
                bindings = {obj: replace(source, observation_id=oid), region: dest}
                step = ExecutableStep(2*n+2, 'fixed_place', 'place', dict(object_id=obj, arm='right', target_id=region), 'place')
                if executable is not None:
                    step = executable.steps[2*n+1]
                result = controller.execute(contract.request(step, bindings=bindings, observation_id=oid))
                report['events'].append(dict(stage=stage, execution=result))
                if not result['completed']:
                    if not (feedback_v2 and execution_recovery and model_planner is not None and model_planner.recovery
                            and result['reason']=='waypoint_timeout'
                            and result['trace'] and result['trace'][-1]['phase'] <= 1):
                        raise ValueError('place_waypoint_incomplete')
                    # No release commanded yet. Positive co-motion evidence still required.
                    records, rgb = probe(stage+'_failure_holding', color, 1.)
                    held = holding_evidence(initial, [r['target_pixel'] for r in records], [r['hand_pixel'] for r in records])
                    report['events'][-1]['failure_holding'] = held
                    if held['status'] != 'holding_supported':
                        raise ValueError('place_timeout_holding_unknown_no_replan')
                    event = dict(error_code='PLACE_WAYPOINT_TIMEOUT', primitive_trace=result,
                        state_source='positive_RGB_holding_comotion_and_no_release_trace', holding_evidence=held)
                    repaired = model_planner.execution_repair(executable.steps[2*n+1:],
                        out/f'{stage}_failure_holding_1.png', completed=n, holding_object=obj, event=event)
                    executable.steps[2*n+1:] = repaired.steps
                    retry_oid = stage+'_place_retry'
                    frame(retry_oid)
                    bindings = {obj: replace(source, observation_id=retry_oid),
                                region: replace(references[region], observation_id=retry_oid)}
                    result = controller.execute(contract.request(executable.steps[2*n+1], bindings=bindings, observation_id=retry_oid))
                    report['events'].append(dict(stage=stage+'_model_repaired_retry', execution=result, recovery_event=event))
                    if not result['completed']:
                        raise ValueError('model_repaired_place_retry_incomplete')
                records, rgb = probe(stage+'_release', color, -1.)
                current = visual_binding(calibration, object_id=obj, observation_id=stage+'_release',
                    pixel=pixel(rgb, color), plane_z=.028)
                release = release_evidence([r['target_pixel'] for r in records], [r['hand_pixel'] for r in records],
                    current.position[:2], dest.position[:2], prior_holding_supported=True,
                    open_retreat_completed=True, single_object_fixture=True, require_destination=not feedback_v2)
                report['events'][-1]['release'] = release
                if release['status'] != 'release_supported':
                    raise ValueError('release_not_supported')
                if feedback_v2:
                    observed = check_observed_goal(records, region)
                    report['events'][-1]['observed_goal'] = observed
                    if observed['status'] == 'unknown':
                        raise ValueError('observed_goal_unknown_no_completion')
                    if observed['status'] == 'not_satisfied':
                        if not (execution_recovery and model_planner is not None and model_planner.recovery):
                            raise ValueError('observed_goal_not_satisfied')
                        event = dict(error_code='OBSERVED_GOAL_NOT_SATISFIED', observed_goal=observed,
                            state_source='RGB_detached_stationary_object_on_assumed_support_plane_not_at_goal', release_evidence=release)
                        repaired = model_planner.execution_repair(executable.steps[2*n:], out/f'{stage}_release_1.png',
                            completed=n, observed_object=obj, event=event)
                        executable.steps[2*n:] = repaired.steps
                        retry_oid = stage+'_goal_retry_pick'
                        rgb = frame(retry_oid); retry_initial = pixel(rgb, color)
                        retry_source = visual_binding(calibration, object_id=obj, observation_id=retry_oid,
                            pixel=retry_initial, plane_z=.028)
                        result = controller.execute(contract.request(executable.steps[2*n], bindings={obj:retry_source}, observation_id=retry_oid))
                        report['events'].append(dict(stage=retry_oid, execution=result, recovery_event=event))
                        if not result['completed']:
                            raise ValueError('goal_repair_pick_incomplete')
                        records, rgb = probe(stage+'_goal_retry_holding', color, 1.)
                        held = holding_evidence(retry_initial, [r['target_pixel'] for r in records], [r['hand_pixel'] for r in records])
                        report['events'][-1]['holding'] = held
                        if held['status'] != 'holding_supported':
                            raise ValueError('goal_repair_holding_unknown')
                        retry_oid = stage+'_goal_retry_place'
                        frame(retry_oid)
                        bindings = {obj:replace(retry_source, observation_id=retry_oid), region:replace(references[region], observation_id=retry_oid)}
                        result = controller.execute(contract.request(executable.steps[2*n+1], bindings=bindings, observation_id=retry_oid))
                        report['events'].append(dict(stage=retry_oid, execution=result))
                        if not result['completed']:
                            raise ValueError('goal_repair_place_incomplete')
                        records, rgb = probe(stage+'_goal_retry_release', color, -1.)
                        current = visual_binding(calibration, object_id=obj, observation_id=retry_oid,
                            pixel=pixel(rgb,color), plane_z=.028)
                        release = release_evidence([r['target_pixel'] for r in records], [r['hand_pixel'] for r in records],
                            current.position[:2], references[region].position[:2], prior_holding_supported=True,
                            open_retreat_completed=True, single_object_fixture=True, require_destination=False)
                        observed = check_observed_goal(records, region)
                        report['events'][-1].update(release=release, observed_goal=observed)
                        if release['status']!='release_supported' or observed['status']!='satisfied':
                            raise ValueError('goal_repair_postcondition_not_supported')
                source_heights[obj] = .028
                if obj == 'blue_candidate' and region == 'return_region':
                    blue_after_first = env.data.body('candidate_blue').xpos.copy()
                elif blue_after_first is not None:
                    protected_distances.append(float(np.linalg.norm(env.data.body('candidate_blue').xpos - blue_after_first)))
                if model_planner is not None and n < len(sequence)-1:
                    observed_name = f'observed_release_{n}'
                    frame(observed_name)
                    offset = 2*n+2
                    remaining = model_planner.remaining(executable.steps[offset:], out / f'{observed_name}.png', completed=n+1)
                    executable.steps[offset:] = remaining.steps
                    report.setdefault('remaining_model_plans', []).append(remaining.to_list())

            # Independent truth scoring AFTER execution, not used to select actions.
            scores = {}
            for name, region in [('candidate_blue', 'return_region'), ('candidate_yellow', 'placement_region')]:
                position = env.data.body(name).xpos.copy()
                target = env.data.body(region).xpos.copy()
                scores[name] = dict(position=position.tolist(), destination=target.tolist(),
                    arrived=bool(np.all(np.abs(position[:2]-target[:2]) <= [.025, .018]) and .015 <= position[2] <= .06))
            report['terminal_scores'] = scores
            report['protected_displacement_samples_m'] = protected_distances
            report['protected_object_preserved'] = max(protected_distances, default=0.) <= .015
            report['success'] = all(s['arrived'] for s in scores.values()) and report['protected_object_preserved']
        except Exception as exc:
            report['failure_stage'] = stage
            report['error'] = f'{type(exc).__name__}: {exc}'
        finally:
            if model_planner is not None:
                report['scope'] = 'live_model_planning_persistent_execution_restricted_fixture'
                report['model_audit'] = model_planner.audit
                report['model_calls'] = sum(a['actual_model_calls'] for a in model_planner.audit)
                report['model_recovery_enabled'] = model_planner.recovery
                report['execution_unknown_policy'] = 'stop_not_fabricate_current_state'
            if env is not None:
                env.close()
            (out / 'summary.json').write_text(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seeds', type=int, nargs='+', default=[0, 1, 2])
    p.add_argument('--reobserve-budget', type=int, choices=[0, 1, 2], default=0)
    args = p.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    rows = []
    for seed in args.seeds:
        row = run(seed, args.output_dir / f'seed_{seed}', reobserve_budget=args.reobserve_budget)
        rows.append(row)
        print('[reference-sequence] '+json.dumps(dict(seed=seed, success=row['success'], failure_stage=row.get('failure_stage'))), flush=True)
    summary = dict(completed_cases=len(rows), successes=sum(r['success'] for r in rows), model_calls=0,
                   scope='backend_feasibility_only', output=str(args.output_dir))
    (args.output_dir / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)
