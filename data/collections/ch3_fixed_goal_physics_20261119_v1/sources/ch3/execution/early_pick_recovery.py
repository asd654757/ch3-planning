"""Authorization limited to controlled-fixture, pre-contact approach timeouts."""
import numpy as np


def early_recovery_gate(pick_result, *, initial_empty_fixture, initial_pixel, fresh_pixel):
    result = {"authorized": False, "reason": "outside_precontact_scope",
              "state_source": "controlled_initial_fixture_plus_precontact_trace_and_rgb",
              "empty_hand_visually_estimated": False}
    if not initial_empty_fixture or pick_result.get("reason") != "waypoint_timeout":
        return result
    trace = pick_result.get("trace", [])
    active = [entry for entry in trace if entry.get("steps", 0) > 0]
    if not active or any(entry.get("phase") != 0 for entry in active):
        return result
    if initial_pixel is None or fresh_pixel is None:
        result["reason"] = "missing_rgb_target"
        return result
    initial, fresh = np.asarray(initial_pixel, float), np.asarray(fresh_pixel, float)
    if initial.shape != (2,) or fresh.shape != (2,) or not np.isfinite([initial, fresh]).all():
        result["reason"] = "invalid_rgb_target"
        return result
    drift = float(np.linalg.norm(fresh - initial))
    result["target_drift_px"] = drift
    if drift > 3:
        result["reason"] = "target_changed_requires_other_state_estimation"
        return result
    result.update(authorized=True, reason="precontact_timeout_with_unchanged_visible_target")
    return result
