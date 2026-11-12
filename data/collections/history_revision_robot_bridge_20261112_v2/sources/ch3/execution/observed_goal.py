"""Restricted RGB-plane goal check; no simulator truth or universal accuracy claim."""
import numpy as np


def observed_goal(position_samples, reference_xy, *, tolerance=(.025, .018)):
    """Two post-release observations against a pre-contact static-region reference.

    The reference is independent of the commanded placement target. Stability
    is necessary but does NOT bound systematic camera/plane calibration error.
    """
    result = dict(status='unknown', source='post_release_RGB_against_initial_static_region_RGB',
                  calibrated_error_bound_available=False)
    try:
        points = np.asarray(position_samples, float)
        reference = np.asarray(reference_xy, float)
        limits = np.asarray(tolerance, float)
        if points.ndim != 2 or points.shape[0] < 2 or points.shape[1] != 2 or reference.shape != (2,) or limits.shape != (2,):
            return result
        if not all(np.isfinite(a).all() for a in (points, reference, limits)) or np.any(limits <= 0):
            return result
        if np.max(np.linalg.norm(points-points[0], axis=1)) > .003:
            result['reason'] = 'post_release_positions_not_stable'
            return result
        errors = np.abs(points-reference)
        inside = np.all(errors <= limits, axis=1)
        # Conflicting observations must not turn into a completed goal.
        status = 'satisfied' if np.all(inside) else ('not_satisfied' if not np.any(inside) else 'unknown')
        result.update(status=status, absolute_errors_m=errors.tolist(),
                      reference_xy=reference.tolist(), position_samples=points.tolist(),
                      tolerance_m=limits.tolist())
    except (ValueError, TypeError):
        pass
    return result
