"""Fixed physical screen for a restricted, known-color sensing feasibility check.

Dimensions are fixture design constants, not fitted to runtime simulator poses.
This does not establish controller reachability or arbitrary occlusion robustness.
"""
from pathlib import Path
import xml.etree.ElementTree as ET

SCREEN = {'position': (.24, .46, .32), 'half_size': (.012, .14, .19)}
VIEWS = ('corner2', 'topview', 'corner')


def add_observation_screen(scene_path: Path):
    tree = ET.parse(scene_path)
    root = tree.getroot()
    world = root.find('worldbody')
    if world is None:
        raise ValueError('missing_worldbody')
    if any(g.get('name') == 'observation_screen' for g in root.iter('geom')):
        raise ValueError('duplicate_observation_screen')
    ET.SubElement(world, 'geom', name='observation_screen', type='box',
        pos=' '.join(map(str, SCREEN['position'])),
        size=' '.join(map(str, SCREEN['half_size'])), rgba='.4 .4 .4 1',
        contype='1', conaffinity='1')
    tree.write(scene_path, encoding='utf-8', xml_declaration=True)


def render_fixed_view(env, camera):
    """Gymnasium's renderer uses camera_id, not env.camera_name.

    Restore the prior render camera so evidence capture cannot silently alter
    the execution controller's observation calibration.
    """
    if camera not in VIEWS:
        raise ValueError('unregistered_observation_camera')
    renderer = env.mujoco_renderer
    previous = renderer.camera_id
    try:
        renderer.camera_id = int(env.model.camera(camera).id)
        return env.render().copy()
    finally:
        renderer.camera_id = previous
