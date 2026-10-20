"""Zero-API physical-screen/alternate-view feasibility; not recovery performance."""
import argparse
import json
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image
from metaworld.asset_path_utils import full_V3_path_for
from ch3.execution.multiobject_scene import build_scene_xml, make_scene
from ch3.execution.occlusion_fixture import add_observation_screen, SCREEN, VIEWS, render_fixed_view
from ch3.execution.visual_holding import color_pixel, color_mask


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--seeds',type=int,nargs='+',default=[730,731,732])
    args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    rows=[]
    for seed in args.seeds:
        for screen in [False,True]:
            out=args.output/f'seed_{seed}_screen_{int(screen)}'
            out.mkdir()
            with tempfile.TemporaryDirectory() as tmp:
                xml=Path(tmp)/'scene.xml'
                build_scene_xml(Path(full_V3_path_for('sawyer_xyz/sawyer_pick_place_v3.xml')),
                    xml,hide_goal_marker=True,add_return_region=True)
                if screen:
                    add_observation_screen(xml)
                env=make_scene(xml,seed=seed)
                try:
                    for _ in range(30):
                        env.step(np.array([0.,0.,0.,-1.]))
                    # Counterfactual render views of the identical state: no step,
                    # reset or object modification between these two captures.
                    before=env.data.qpos.copy()
                    views={}
                    for camera in VIEWS:
                        rgb=render_fixed_view(env,camera)
                        Image.fromarray(np.flip(rgb,(0,1))).save(out/f'{camera}.png')
                        colors={}
                        for color in ['blue','yellow']:
                            try:
                                center,_=color_pixel(rgb,color,require_unique=True)
                                colors[color]=dict(status='visible',pixel=list(center))
                            except ValueError as exc:
                                colors[color]=dict(status='unknown',reason=str(exc))
                            colors[color]['mask_pixels']=int(color_mask(rgb,color).sum())
                        views[camera]=colors
                    row=dict(seed=seed,screen=screen,views=views,
                        render_only_state_unchanged=bool(np.array_equal(before,env.data.qpos)),
                        episode_resets=int(env._ch3_reset_count),actual_api_calls=0,
                        scope='initial_scene_observation_feasibility_not_execution_or_goal_success')
                    rows.append(row)
                    print(json.dumps(row),flush=True)
                finally:
                    env.close()
    result=dict(screen_geometry=SCREEN,views=list(VIEWS),episodes=rows,
        limitations=['Known colors and calibrated fixed cameras.',
            'Camera switch is a simulated observation action, not robot head motion.',
            'Physical reachability/contact effects with screen not yet tested.',
            'Visibility alone is not a goal-completion or uncertainty-calibration certificate.'])
    (args.output/'view_check.json').write_text(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
