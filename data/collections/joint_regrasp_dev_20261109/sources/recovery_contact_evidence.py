"""Restricted closure diagnostics, NOT an empty-gripper sensor.

Small jaw distance was observed during real holding; its geometry-only empty
claim is invalid. No contacts, object poses or task scores enter.
"""
from math import isfinite


def aperture_evidence(samples, *, minimum_object_span=.036, finger_pad_inset=.012,
                      separation_margin=.006, minimum_samples=5):
    result = {"status": "unknown", "source": "robot_proprioception_with_registered_geometry",
              "empty_supported": False, "holding_supported": False, "closure_supported": False,
              "assumptions": {"minimum_object_span_m": minimum_object_span,
                              "body_center_to_inner_pad_offset_m": finger_pad_inset},
              "limitation": "registered rigid object minimum span; not arbitrary open-world contents"}
    if not all(isfinite(v) and v > 0 for v in (minimum_object_span, finger_pad_inset, separation_margin)):
        raise ValueError("finite positive geometry required")
    if len(samples) < minimum_samples:
        return result
    try:
        distances = [float(s["jaw_body_distance_m"]) for s in samples]
        heights = [float(s["hand_z_m"]) for s in samples]
        closing = all(s["commanded_gripper"] == 1 for s in samples)
    except (KeyError, TypeError, ValueError):
        return result
    if not closing or not all(isfinite(d) and d >= 0 for d in distances) or not all(
            isfinite(z) and z >= .12 for z in heights):
        return result
    aperture = [max(0., d-finger_pad_inset) for d in distances]
    result.update(max_inner_aperture_m=max(aperture), min_inner_aperture_m=min(aperture),
                  stability_range_m=max(distances)-min(distances))
    if max(distances)-min(distances) <= .001 and max(aperture) < minimum_object_span-separation_margin:
        result.update(status="closure_supported_not_empty",closure_supported=True)
    return result


def regrasp_gate(aperture, nonfollowing, *, target_visible, observation_id,
                 sole_grasp_candidate=None, initial_empty_known=False, holding=None):
    if not observation_id:
        raise ValueError("fresh observation id required")
    authorized = bool(aperture.get("closure_supported") and
                      nonfollowing.get("status") == "target_not_following_supported" and target_visible
                      and sole_grasp_candidate == "blue_candidate" and initial_empty_known
                      and (holding or {}).get("status") != "holding_supported")
    return {"authorized": authorized, "observation_id": observation_id,
            "reason": "single_candidate_closed_fixture_and_stationary_target" if authorized else "insufficient_combined_evidence",
            "assumptions": ["known initially empty hand; only blue could have been grasped",
                            "fixed calibrated support plane; stationary separated target",
                            "no new object or unrecorded manipulation; jaw distance alone is not emptiness"],
            "scope": "restricted_fixture_postcontact_regrasp_not_open_world"}
