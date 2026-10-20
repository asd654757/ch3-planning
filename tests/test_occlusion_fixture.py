from types import SimpleNamespace
import xml.etree.ElementTree as ET
import numpy as np
import pytest
from ch3.execution.occlusion_fixture import add_observation_screen, render_fixed_view


def test_screen_is_real_collision_geometry_and_no_camera_rewrite(tmp_path):
    path=tmp_path/'scene.xml'
    path.write_text('<mujoco><worldbody><camera name="corner2"/></worldbody></mujoco>')
    add_observation_screen(path)
    root=ET.parse(path).getroot()
    geom=root.find('.//geom')
    assert geom.get('contype')=='1' and geom.get('conaffinity')=='1'
    assert geom.get('rgba').endswith('1')
    assert root.find('.//camera').get('name')=='corner2'
    with pytest.raises(ValueError):
        add_observation_screen(path)


def test_camera_id_switch_and_restore_even_on_error():
    renderer=SimpleNamespace(camera_id=2)
    model=SimpleNamespace(camera=lambda name:SimpleNamespace(id=0))
    captured=[]
    def render():
        captured.append(renderer.camera_id)
        return np.zeros((2,2,3))
    env=SimpleNamespace(mujoco_renderer=renderer,model=model,render=render)
    render_fixed_view(env,'topview')
    assert captured==[0] and renderer.camera_id==2
    def fail():
        raise RuntimeError('render failure')
    env.render=fail
    with pytest.raises(RuntimeError):render_fixed_view(env,'topview')
    assert renderer.camera_id==2
