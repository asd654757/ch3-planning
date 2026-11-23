"""Task-level ROUTED gate for the registered Pi05 LIBERO rollout."""

from hashlib import sha256

import numpy as np

from ch3.execution.recovery_context import RecoveryContext, recovery_route


_REGISTERED_TASKS = {
    ("libero_object", 0): "pick up the alphabet soup and place it in the basket",
}


def _public_observation_identity(observation):
    """Identify the available model inputs without extracting task effects."""
    try:
        pixels = observation["pixels"]
        robot = observation["robot_state"]
        images = tuple(np.asarray(pixels[key]) for key in ("image", "image2"))
        proprio = tuple(np.asarray(value) for value in
                        (robot["eef"]["pos"], robot["eef"]["quat"], robot["gripper"]["qpos"]))
    except (KeyError, TypeError):
        return None
    if any(im.dtype != np.uint8 or im.ndim != 4 or im.shape[0] != 1
           or im.shape[-1] != 3 or min(im.shape[1:3]) < 2 for im in images):
        return None
    if any(x.shape != (1, size) or x.dtype.kind != "f" or not np.isfinite(x).all()
           for x, size in zip(proprio, (3, 4, 2), strict=True)):
        return None
    digest = sha256()
    for value in (*images, *proprio):
        digest.update(str((value.shape, value.dtype.str)).encode())
        digest.update(np.ascontiguousarray(value))
    return digest.hexdigest()


class LiberoTaskGate:
    """Bind every dispatched action to current public inputs and the native task.

    This adapter can assess input availability. It has no object predicates and
    accepts no reward, simulator success flag or privileged object state.
    """

    def __init__(self, suite, task_id, description, *, max_reobservations=2):
        if _REGISTERED_TASKS.get((suite, task_id)) != description:
            raise ValueError("unregistered LIBERO task or changed native instruction")
        if type(max_reobservations) is not int or max_reobservations < 0:
            raise ValueError("a finite nonnegative reobservation budget is required")
        self.context = RecoveryContext(description, goals=())
        self.max_reobservations = max_reobservations

    def observe(self, observation):
        identity = _public_observation_identity(observation)
        self.context.observe(facts=(), image_sha256=identity or "unavailable",
                             evidence_source="libero_rgb_and_robot_state",
                             status="supported" if identity else "unknown")
        return self.context.observation_status

    def route(self, reobservations_used):
        return recovery_route(execution_status="not_started", next_prerequisites=self.context.observation_status,
                              intent="aligned", reobservations_used=reobservations_used,
                              max_reobservations=self.max_reobservations)

    def dispatch(self, ticket, action, action_space):
        action = np.asarray(action)
        if action.shape != (1, 7) or action.dtype.kind != "f" or not np.isfinite(action).all():
            raise ValueError("Pi05 produced an invalid LIBERO action")
        # Same saturation as the registered relative arm controller and its
        # sign-based gripper. Record both outputs for comparison with baseline.
        bounded = np.clip(action, action_space.low, action_space.high)
        self.context.consume_request(ticket)
        return bounded
