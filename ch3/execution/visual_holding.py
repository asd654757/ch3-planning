"""Restricted RGB/proprioceptive holding evidence, not general perception.

Only a known, uniquely colored target is supported. Missing or contradictory
evidence returns unknown; simulator object poses must never enter this module.
"""
import numpy as np


def color_pixel(frame, color):
    rgb = np.asarray(frame, dtype=float)
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("RGB image required")
    if color == "blue":
        mask = (rgb[:, :, 2] > 75) & (rgb[:, :, 2] > 1.6 * rgb[:, :, 0]) & (rgb[:, :, 2] > 1.4 * rgb[:, :, 1])
    elif color == "green":
        mask = (rgb[:, :, 1] > 75) & (rgb[:, :, 1] > 1.6 * rgb[:, :, 0]) & (rgb[:, :, 1] > 1.4 * rgb[:, :, 2])
    else:
        raise ValueError("unsupported color")
    y, x = np.nonzero(mask)
    if len(x) < 20:
        raise ValueError("insufficient target color evidence")
    return (float(np.median(x)), float(np.median(y))), int(len(x))


def holding_evidence(initial_pixel, target_pixels, hand_pixels):
    """Two or more distinct post-pick frames must support co-motion.

    Thresholds are fixed pixel-space heuristics for the 480px calibrated scene.
    A positive result is observational evidence, not force/contact verification.
    """
    result = {"status": "unknown", "reason": "insufficient_visual_evidence"}
    if initial_pixel is None or len(target_pixels) < 2 or len(target_pixels) != len(hand_pixels):
        return result
    if any(p is None for p in target_pixels + hand_pixels):
        return result
    targets, hands = np.asarray(target_pixels, float), np.asarray(hand_pixels, float)
    initial = np.asarray(initial_pixel, float)
    if targets.shape != (len(target_pixels), 2) or hands.shape != targets.shape or initial.shape != (2,):
        return result
    if not all(np.isfinite(a).all() for a in (targets, hands, initial)):
        return result
    displacement = float(np.linalg.norm(targets[-1] - initial))
    hand_motion = float(np.linalg.norm(hands[-1] - hands[0]))
    target_motion = float(np.linalg.norm(targets[-1] - targets[0]))
    offset_drift = float(np.linalg.norm((targets[-1] - hands[-1]) - (targets[0] - hands[0])))
    max_distance = float(np.max(np.linalg.norm(targets - hands, axis=1)))
    result.update(displacement_px=displacement, hand_motion_px=hand_motion,
                  target_motion_px=target_motion, offset_drift_px=offset_drift,
                  max_target_hand_distance_px=max_distance)
    if displacement >= 8 and hand_motion >= 4 and target_motion >= 4 and offset_drift <= 6 and max_distance <= 25:
        result.update(status="holding_supported", reason="target_displacement_and_hand_comotion")
    else:
        result["reason"] = "comotion_or_proximity_not_supported"
    return result
