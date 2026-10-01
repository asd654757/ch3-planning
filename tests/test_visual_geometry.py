from dataclasses import replace

import numpy as np
import pytest

from ch3.execution.visual_geometry import CameraCalibration, pixel_to_plane, visual_binding


def camera():
    transform = np.eye(4)
    transform[:3, :3] = np.diag([1, -1, -1])
    transform[2, 3] = 1
    return CameraCalibration(np.array([[100., 0, 50], [0, 100., 50], [0, 0, 1]]),
                             transform, 100, 100, "robot_base")


def test_center_ray_hits_plane():
    assert pixel_to_plane(camera(), pixel=(50, 50), plane_z=0) == (0., 0., 0.)


def test_optical_y_and_execution_y_convention():
    assert pixel_to_plane(camera(), pixel=(60, 60), plane_z=0) == pytest.approx((.1, -.1, 0))


@pytest.mark.parametrize("pixel,z", [((-1, 50), 0), ((100, 50), 0), ((50, 50), 2), ((50, 50), float("nan"))])
def test_invalid_intersections(pixel, z):
    with pytest.raises(ValueError):
        pixel_to_plane(camera(), pixel=pixel, plane_z=z)


def test_reject_reflection_transform():
    transform = np.eye(4)
    transform[0, 0] = -1
    with pytest.raises(ValueError, match="proper orthonormal"):
        pixel_to_plane(replace(camera(), camera_to_execution=transform), pixel=(50, 50), plane_z=0)


def test_binding_records_visual_source():
    binding = visual_binding(camera(), object_id="red", observation_id="frame_2",
                             pixel=(50, 50), plane_z=0)
    assert binding.source == "visual_estimate"
    assert binding.observation_id == "frame_2"


def test_flip_is_involution():
    from ch3.execution.visual_geometry import display_to_native_pixel
    original = (20., 30.)
    converted = display_to_native_pixel(original, width=100, height=80, flip_both_axes=True)
    assert converted == (79., 49.)
    assert display_to_native_pixel(converted, width=100, height=80, flip_both_axes=True) == original


def test_projection_roundtrip():
    from ch3.execution.visual_geometry import project_point
    point = (.1, -.1, 0.)
    pixel = project_point(camera(), point)
    assert pixel_to_plane(camera(), pixel=pixel, plane_z=0.) == pytest.approx(point)
