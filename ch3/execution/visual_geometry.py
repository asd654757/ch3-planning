"""Calibrated pixel-to-plane geometry; no simulator object state inputs.

Convention: camera optical axes are x right, y down, z forward. Extrinsics
map optical-camera coordinates into the named execution frame. Image flips
must be undone by the caller before supplying native pixel coordinates.
"""

from dataclasses import dataclass

import numpy as np

from ch3.execution.skill_contract import TargetBinding


@dataclass(frozen=True)
class CameraCalibration:
    intrinsics: np.ndarray
    camera_to_execution: np.ndarray
    width: int
    height: int
    coordinate_frame: str

    def validate(self):
        k = np.asarray(self.intrinsics, dtype=float)
        t = np.asarray(self.camera_to_execution, dtype=float)
        if k.shape != (3, 3) or t.shape != (4, 4):
            raise ValueError("invalid calibration shape")
        if not np.isfinite(k).all() or not np.isfinite(t).all():
            raise ValueError("nonfinite calibration")
        if self.width <= 0 or self.height <= 0 or not self.coordinate_frame:
            raise ValueError("missing image dimensions or frame")
        if k[0, 0] <= 0 or k[1, 1] <= 0 or not np.allclose(k[2], [0, 0, 1]):
            raise ValueError("invalid pinhole intrinsics")
        if not np.allclose(t[3], [0, 0, 0, 1]):
            raise ValueError("invalid homogeneous transform")
        rotation = t[:3, :3]
        if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6) or not np.isclose(np.linalg.det(rotation), 1):
            raise ValueError("extrinsic rotation must be proper orthonormal")


def pixel_to_plane(calibration: CameraCalibration, *, pixel: tuple[float, float],
                   plane_z: float) -> tuple[float, float, float]:
    """Intersect a forward camera ray with an assumed horizontal plane.

    Plane height is an explicit modelling assumption, not a measured object
    height. This computes a point only, not a grasp pose or collision-free path.
    """
    calibration.validate()
    u, v = pixel
    if not np.isfinite([u, v, plane_z]).all() or not (0 <= u < calibration.width and 0 <= v < calibration.height):
        raise ValueError("pixel outside image or nonfinite plane")
    transform = np.asarray(calibration.camera_to_execution, dtype=float)
    origin = transform[:3, 3]
    ray = transform[:3, :3] @ np.linalg.solve(calibration.intrinsics, [u, v, 1.])
    if abs(ray[2]) < 1e-8:
        raise ValueError("ray parallel to plane")
    distance = (plane_z - origin[2]) / ray[2]
    if distance <= 0:
        raise ValueError("plane intersection is behind camera")
    return tuple(float(x) for x in origin + distance * ray)


def visual_binding(calibration: CameraCalibration, *, object_id: str,
                   observation_id: str, pixel: tuple[float, float], plane_z: float) -> TargetBinding:
    if not object_id or not observation_id:
        raise ValueError("missing object or observation identity")
    return TargetBinding(object_id, pixel_to_plane(calibration, pixel=pixel, plane_z=plane_z),
                         calibration.coordinate_frame, observation_id, "visual_estimate")


def display_to_native_pixel(pixel, *, width: int, height: int, flip_both_axes: bool):
    u, v = pixel
    if not np.isfinite([u, v]).all() or not (0 <= u < width and 0 <= v < height):
        raise ValueError("display pixel outside image")
    return (width - 1 - u, height - 1 - v) if flip_both_axes else (u, v)


def project_point(calibration: CameraCalibration, point):
    calibration.validate()
    p = np.asarray(point, dtype=float)
    if p.shape != (3,) or not np.isfinite(p).all():
        raise ValueError("invalid projection point")
    transform = np.asarray(calibration.camera_to_execution)
    optical = transform[:3, :3].T @ (p - transform[:3, 3])
    if optical[2] <= 0:
        raise ValueError("point behind camera")
    image = calibration.intrinsics @ optical
    return tuple(float(x) for x in image[:2] / image[2])


def fixed_mujoco_calibration(model, data, *, camera: str, width: int, height: int):
    """Export static camera calibration only, never scene object positions.

    Output frame is MuJoCo world, not robot_base. A separate calibrated rigid
    transform is required before a robot-base execution backend can consume it.
    """
    camera_id = model.camera(camera).id
    if int(model.cam_mode[camera_id]) != 0 or int(model.cam_bodyid[camera_id]) != 0:
        raise ValueError("only fixed world cameras are supported")
    # Older MuJoCo releases only support perspective cameras and do not expose
    # cam_orthographic; newer releases require the explicit rejection below.
    orthographic = getattr(model, "cam_orthographic", None)
    if orthographic is not None and bool(orthographic[camera_id]):
        raise ValueError("orthographic camera unsupported")
    focal = .5 * height / np.tan(np.deg2rad(float(model.cam_fovy[camera_id])) / 2)
    intrinsics = np.array([[focal, 0, (width - 1) / 2],
                           [0, focal, (height - 1) / 2], [0, 0, 1.]])
    transform = np.eye(4)
    transform[:3, :3] = data.cam_xmat[camera_id].reshape(3, 3) @ np.diag([1., -1., -1.])
    transform[:3, 3] = data.cam_xpos[camera_id]
    result = CameraCalibration(intrinsics, transform, width, height, "mujoco_world")
    result.validate()
    return result
