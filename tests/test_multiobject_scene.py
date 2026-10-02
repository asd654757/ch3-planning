import xml.etree.ElementTree as ET

from ch3.execution.multiobject_scene import build_scene_xml


def test_hidden_marker_keeps_native_site(tmp_path):
    source = tmp_path / "source.xml"
    source.write_text('<mujoco><worldbody/><worldbody><site name="goal" rgba="0 0 1 1"/></worldbody></mujoco>')
    output = tmp_path / "scene.xml"
    build_scene_xml(source, output, hide_goal_marker=True)
    assert next(ET.parse(output).getroot().iter("site")).get("rgba") == "0 0 0 0"


def test_scene_uses_free_objects_and_absolute_includes(tmp_path):
    source = tmp_path / "source.xml"
    source.write_text('<mujoco><include file="scene.xml"/><worldbody><body name="obj"><geom material="wood"/></body></worldbody></mujoco>')
    output = tmp_path / "generated.xml"
    build_scene_xml(source, output)
    root = ET.parse(output).getroot()
    assert root.find("include").get("file") == str(tmp_path / "scene.xml")
    world = root.find("worldbody")
    for name in ("candidate_blue", "candidate_yellow"):
        assert world.find(f"body[@name='{name}']/freejoint") is not None
    assert world.find("body[@name='placement_region']/geom").get("type") == "box"
    assert "material" not in world.find("body[@name='obj']/geom").attrib


def test_nested_includes_resolve_original_asset_paths(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "mesh.stl").write_bytes(b"fixture")
    (assets / "part.xml").write_text('<mujocoinclude><asset><mesh file="mesh.stl"/></asset></mujocoinclude>')
    source = assets / "source.xml"
    source.write_text('<mujoco><include file="part.xml"/><worldbody/></mujoco>')
    output = tmp_path / "generated.xml"
    build_scene_xml(source, output)
    root = ET.parse(output).getroot()
    assert root.find("include") is None
    assert root.find("asset/mesh").get("file") == str(assets / "mesh.stl")


def test_optional_return_marker_does_not_change_old_scene(tmp_path):
    source = tmp_path / "source.xml"
    source.write_text('<mujoco><worldbody/></mujoco>')
    output = tmp_path / "scene.xml"
    build_scene_xml(source, output)
    assert ET.parse(output).getroot().find(".//body[@name='return_region']") is None
    build_scene_xml(source, output, add_return_region=True)
    assert ET.parse(output).getroot().find(".//body[@name='return_region']/geom").get("rgba") == "0.8 0.05 0.8 1"
