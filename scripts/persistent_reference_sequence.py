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


def run(seed, out):
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
                  protected_displacement_limit_m=.015, episode_step_limit=1000)
    env = None
    stage = 'setup'
    with tempfile.TemporaryDirectory() as tmp:
        try:
            xml = Path(tmp) / 'scene.xml'
            build_scene_xml(Path(full_V3_path_for('sawyer_xyz/sawyer_pick_place_v3.xml')), xml,
                            hide_goal_marker=True, add_return_region=True)
            env = make_scene(xml, seed=seed)
            report['episode_resets'] = 1
            env.max_path_length = 1000
            for _ in range(30):
                env.step(np.array([0., 0., 0., -1.]))
            calibration = fixed_mujoco_calibration(env.model, env.data, camera='corner2', width=480, height=480)
            controller = FixedPickPlaceController(env)
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

            blue_after_first = None
            protected_distances = []  # evaluator-only snapshots, never gates
            for n, (obj, color, region, region_color) in enumerate([
                ('blue_candidate', 'blue', 'return_region', 'magenta'),
                ('yellow_candidate', 'yellow', 'green_region', 'green')]):
                stage = f'{color}_pick'
                print(f'[reference-sequence] seed={seed} stage={stage}', flush=True)
                rgb = frame(stage)
                initial = pixel(rgb, color)
                oid = stage
                source = visual_binding(calibration, object_id=obj, observation_id=oid, pixel=initial, plane_z=.02)
                step = ExecutableStep(2*n+1, 'fixed_pick', 'grasp', dict(object_id=obj, arm='right'), 'pick')
                result = controller.execute(contract.request(step, bindings={obj: source}, observation_id=oid))
                report['events'].append(dict(stage=stage, execution=result))
                if not result['completed']:
                    raise ValueError('pick_waypoint_incomplete')
                records, rgb = probe(stage+'_holding', color, 1.)
                held = holding_evidence(initial, [r['target_pixel'] for r in records], [r['hand_pixel'] for r in records])
                report['events'][-1]['holding'] = held
                if held['status'] != 'holding_supported':
                    raise ValueError('holding_not_supported')
                if blue_after_first is not None:
                    protected_distances.append(float(np.linalg.norm(env.data.body('candidate_blue').xpos - blue_after_first)))
                stage = f'{color}_place'
                print(f'[reference-sequence] seed={seed} stage={stage}', flush=True)
                dest = visual_region_binding(calibration, frame=rgb, color=region_color,
                    object_id=region, observation_id=stage, plane_z=.008)
                # Object binding is required by dispatch but place targets only the region.
                from dataclasses import replace
                bindings = {obj: replace(source, observation_id=stage), region: dest}
                step = ExecutableStep(2*n+2, 'fixed_place', 'place', dict(object_id=obj, arm='right', target_id=region), 'place')
                result = controller.execute(contract.request(step, bindings=bindings, observation_id=stage))
                report['events'].append(dict(stage=stage, execution=result))
                if not result['completed']:
                    raise ValueError('place_waypoint_incomplete')
                records, rgb = probe(stage+'_release', color, -1.)
                current = visual_binding(calibration, object_id=obj, observation_id=stage+'_release',
                    pixel=pixel(rgb, color), plane_z=.028)
                release = release_evidence([r['target_pixel'] for r in records], [r['hand_pixel'] for r in records],
                    current.position[:2], dest.position[:2], prior_holding_supported=True,
                    open_retreat_completed=True, single_object_fixture=True)
                report['events'][-1]['release'] = release
                if release['status'] != 'release_supported':
                    raise ValueError('release_not_supported')
                if n == 0:
                    blue_after_first = env.data.body('candidate_blue').xpos.copy()
                else:
                    protected_distances.append(float(np.linalg.norm(env.data.body('candidate_blue').xpos - blue_after_first)))

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
            if env is not None:
                env.close()
            (out / 'summary.json').write_text(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seeds', type=int, nargs='+', default=[0, 1, 2])
    args = p.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    rows = []
    for seed in args.seeds:
        row = run(seed, args.output_dir / f'seed_{seed}')
        rows.append(row)
        print('[reference-sequence] '+json.dumps(dict(seed=seed, success=row['success'], failure_stage=row.get('failure_stage'))), flush=True)
    summary = dict(completed_cases=len(rows), successes=sum(r['success'] for r in rows), model_calls=0,
                   scope='backend_feasibility_only', output=str(args.output_dir))
    (args.output_dir / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)
