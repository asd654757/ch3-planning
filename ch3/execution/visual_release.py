"""Restricted release evidence after supported holding, not general empty-hand sensing.

A single-held-object controlled fixture and an open-gripper retreat trace are
required. RGB evidence supports detachment/arrival, not contact-force safety.
"""
import numpy as np


def release_evidence(target_pixels, hand_pixels, target_world_xy, destination_xy, *,
                     prior_holding_supported, open_retreat_completed,
                     single_object_fixture, destination_tolerance=(.025, .018),
                     require_destination=True):
    result = {"status": "unknown", "empty_hand_supported": False,
              "reason": "release_evidence_insufficient",
              "scope": "single_previously_held_object_with_open_retreat_and_visible_stationarity"}
    if not (prior_holding_supported and open_retreat_completed and single_object_fixture):
        return result
    if len(target_pixels) < 2 or len(target_pixels) != len(hand_pixels):
        return result
    if any(p is None for p in target_pixels + hand_pixels):
        return result
    target, hand = np.asarray(target_pixels, float), np.asarray(hand_pixels, float)
    xy, destination = np.asarray(target_world_xy, float), np.asarray(destination_xy, float)
    if target.shape != (len(target_pixels), 2) or hand.shape != target.shape or xy.shape != (2,) or destination.shape != (2,):
        return result
    if not all(np.isfinite(a).all() for a in (target, hand, xy, destination)):
        return result
    drift = float(np.max(np.linalg.norm(target - target[0], axis=1)))
    separation = float(np.min(np.linalg.norm(target - hand, axis=1)))
    hand_motion = float(np.linalg.norm(hand[-1] - hand[0]))
    arrived = bool(np.all(np.abs(xy - destination) <= destination_tolerance))
    result.update(target_drift_px=drift, hand_separation_px=separation,
                  hand_motion_px=hand_motion, destination_supported=arrived,
                  location_source="RGB_centroid_assumed_support_plane_not_truth")
    if drift <= 3 and separation >= 18 and hand_motion >= 4 and (arrived or not require_destination):
        result.update(status="release_supported", empty_hand_supported=True,
                      reason=("stationary_target_at_destination_during_open_hand_retreat" if require_destination
                              else "stationary_target_detached_during_open_hand_retreat_not_goal_completion"))
    return result
