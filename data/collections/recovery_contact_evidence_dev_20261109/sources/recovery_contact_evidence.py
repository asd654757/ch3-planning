"""Restricted empty-gripper evidence from robot proprioception only.

Not an open-world empty-hand sensor: the registered object minimum span and
gripper geometry must be valid. No contacts, object poses or task scores enter.
"""
from math import isfinite


def aperture_evidence(samples, *, minimum_object_span=.036, finger_pad_inset=.012,
                      separation_margin=.006, minimum_samples=5):
    result = {"status": "unknown", "source": "robot_proprioception_with_registered_geometry",
              "empty_supported": False, "holding_supported": False,
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
        result.update(status="empty_supported",empty_supported=True)
    return result


def regrasp_gate(aperture, nonfollowing, *, target_visible, observation_id):
    if not observation_id:
        raise ValueError("fresh observation id required")
    authorized = bool(aperture.get("empty_supported") and
                      nonfollowing.get("status") == "target_not_following_supported" and target_visible)
    return {"authorized": authorized, "observation_id": observation_id,
            "reason": "empty_aperture_and_stationary_visible_target" if authorized else "insufficient_combined_evidence",
            "assumptions": ["registered object minimum span", "fixed calibrated support plane",
                            "separated high hand probe; no unknown thin object"],
            "scope": "restricted_fixture_postcontact_regrasp_not_open_world"}
