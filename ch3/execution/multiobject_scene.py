"""Experimental persistent scene construction, not a benchmark executor."""

from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET


def build_scene_xml(source: Path, destination: Path, *, hide_goal_marker: bool = False, add_return_region: bool = False) -> None:
    tree = ET.parse(source)
    root = tree.getroot()
    def expand(parent, directory):
        for element in list(parent):
            if "file" in element.attrib:
                relative = element.attrib["file"]
                candidate = source.parent / relative
                if not candidate.is_file():
                    candidate = directory / relative
                candidate = candidate.resolve()
                element.set("file", str(candidate))
                if element.tag == "include" and candidate.is_file():
                    included = ET.parse(candidate).getroot()
                    expand(included, candidate.parent)
                    index = list(parent).index(element)
                    parent.remove(element)
                    for child in list(included):
                        parent.insert(index, child)
                        index += 1
                    continue
            expand(element, directory)

    expand(root, source.parent)
    world = root.find("worldbody")
    if world is None:
        raise ValueError("source scene has no worldbody")
    if hide_goal_marker:
        # Keep the named site for native reset code, but exclude it from RGB.
        # Expanded includes may contribute multiple worldbody elements.
        for site in root.iter("site"):
            if site.get("name") == "goal":
                site.set("rgba", "0 0 0 0")
    for name, rgba, x in (("candidate_blue", "0.05 0.2 0.95 1", -0.12),
                          ("candidate_yellow", "0.95 0.8 0.05 1", 0.12)):
        body = ET.SubElement(world, "body", name=name, pos=f"{x} 0.68 0.025")
        ET.SubElement(body, "freejoint", name=f"{name}_joint")
        ET.SubElement(body, "inertial", pos="0 0 0", mass="0.08",
                      diaginertia="0.0000193 0.0000193 0.0000173")
        ET.SubElement(body, "geom", name=f"{name}_geom", type="box",
                      size="0.018 0.018 0.02", mass="0.08", rgba=rgba,
                      friction="1 0.1 0.002", condim="4")
    body = ET.SubElement(world, "body", name="placement_region", pos="0 0.84 0.004")
    ET.SubElement(body, "geom", name="placement_region_geom", type="box",
                  size="0.07 0.045 0.004", rgba="0.05 0.75 0.3 1")
    if add_return_region:
        # Optional visible semantic destination. No online code reads its pose.
        body = ET.SubElement(world, "body", name="return_region", pos="-0.15 0.79 0.004")
        ET.SubElement(body, "geom", name="return_region_geom", type="box",
                      size="0.05 0.035 0.004", rgba="0.8 0.05 0.8 1")
    # Give the native puck a visible solid color rather than a wood texture.
    native = world.find("body[@name='obj']/geom")
    if native is not None:
        native.attrib.pop("material", None)
        native.set("rgba", "0.95 0.05 0.05 1")
    tree.write(destination, encoding="utf-8", xml_declaration=True)


def make_scene(xml_path: Path, *, seed: int, size: int = 480):
    import numpy as np
    from metaworld.envs.sawyer_pick_place_v3 import SawyerPickPlaceEnvV3

    class Scene(SawyerPickPlaceEnvV3):
        @property
        def model_name(self):
            return str(xml_path)

        def evaluate_state(self, obs, action):
            # Native single-puck reward is not a multi-object success metric.
            return 0.0, {"scene_only": True}

    env = Scene(render_mode="rgb_array", camera_name="corner2", height=size, width=size)
    env._set_task_called = True
    env._freeze_rand_vec = False
    env.seed(seed)
    env.reset()
    rng = np.random.default_rng(seed)
    for name, x in (("candidate_blue", -0.12), ("candidate_yellow", 0.12)):
        joint = env.model.joint(f"{name}_joint")
        index = int(joint.qposadr[0])
        env.data.qpos[index:index + 3] = [x, 0.68 + rng.uniform(-0.025, 0.025), 0.025]
        env.data.qpos[index + 3:index + 7] = [1, 0, 0, 0]
    import mujoco
    mujoco.mj_forward(env.model, env.data)
    return env
