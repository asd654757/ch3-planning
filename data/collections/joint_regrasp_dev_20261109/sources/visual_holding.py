"""Restricted RGB/proprioceptive holding evidence, not general perception.

Only a known, uniquely colored target is supported. Missing or contradictory
evidence returns unknown; simulator object poses must never enter this module.
"""
import numpy as np


def color_mask(frame, color):
    rgb = np.asarray(frame, dtype=float)
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("RGB image required")
    if color == "blue":
        mask = (rgb[:, :, 2] > 75) & (rgb[:, :, 2] > 1.6 * rgb[:, :, 0]) & (rgb[:, :, 2] > 1.4 * rgb[:, :, 1])
    elif color == "green":
        mask = (rgb[:, :, 1] > 75) & (rgb[:, :, 1] > 1.6 * rgb[:, :, 0]) & (rgb[:, :, 1] > 1.4 * rgb[:, :, 2])
    elif color == "yellow":
        mask = (rgb[:, :, 0] > 100) & (rgb[:, :, 1] > 85) & (rgb[:, :, 2] < .55 * np.minimum(rgb[:, :, 0], rgb[:, :, 1]))
    elif color == "magenta":
        mask = (rgb[:, :, 0] > 90) & (rgb[:, :, 2] > 90) & (rgb[:, :, 1] < .55 * np.minimum(rgb[:, :, 0], rgb[:, :, 2]))
    else:
        raise ValueError("unsupported color")
    return mask


def color_pixel(frame, color, *, require_unique=False, pixel_statistic="median"):
    if pixel_statistic not in {"median", "bbox_center"}:
        raise ValueError("unsupported pixel statistic")
    mask = color_mask(frame, color)
    y, x = np.nonzero(mask)
    if len(x) < 20:
        raise ValueError("insufficient target color evidence")
    if require_unique:
        # 8-connected components; fail closed when another sizeable candidate
        # is visible. No truth identity or nearest-to-answer selection.
        remaining = set(zip(y.tolist(), x.tolist()))
        components = []
        while remaining:
            seed = remaining.pop()
            stack, component = [seed], [seed]
            while stack:
                row, col = stack.pop()
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        neighbor = (row + dy, col + dx)
                        if neighbor in remaining:
                            remaining.remove(neighbor)
                            stack.append(neighbor)
                            component.append(neighbor)
            if len(component) >= 20:
                components.append(component)
        # Gripper occlusion can split ONE colored cube into adjacent patches.
        # Group only patches whose bounding-box pixel gap is <=3; distant
        # same-color candidates still reject. This is a restricted adapter,
        # not proof of object identity for nearby same-color objects.
        groups = [np.asarray(c) for c in components]
        merged = True
        while merged:
            merged = False
            for i in range(len(groups)):
                for j in range(i + 1, len(groups)):
                    low_i, high_i = groups[i].min(0), groups[i].max(0)
                    low_j, high_j = groups[j].min(0), groups[j].max(0)
                    gap = np.maximum(np.maximum(low_i - high_j, low_j - high_i), 0)
                    if np.max(gap) <= 3:
                        groups[i] = np.concatenate([groups[i], groups[j]])
                        groups.pop(j)
                        merged = True
                        break
                if merged:
                    break
        if len(groups) != 1:
            raise ValueError("missing or ambiguous color candidates")
        coords = groups[0]
        y, x = coords[:, 0], coords[:, 1]
    if pixel_statistic == "bbox_center":
        return (float((x.min() + x.max()) / 2), float((y.min() + y.max()) / 2)), int(len(x))
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


def failed_pick_evidence(initial_pixel, target_pixels, hand_pixels):
    """Restricted evidence that the visible target did not follow a hand probe.

    This is NOT empty-hand or table-contact estimation. In particular another
    object might be held, and a 2-D projection cannot establish contact. No
    executable WorldState facts or retry permission are emitted here.
    """
    result = {"status": "unknown", "reason": "insufficient_visual_evidence",
              "empty_hand_supported": False, "retry_authorized": False,
              "observed_facts": []}
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
    # Check every frame rather than merely matching the two endpoints.
    initial_drift = float(np.max(np.linalg.norm(targets - initial, axis=1)))
    target_motion = float(np.max(np.linalg.norm(targets - targets[0], axis=1)))
    hand_motion = float(np.linalg.norm(hands[-1] - hands[0]))
    separation = float(np.min(np.linalg.norm(targets - hands, axis=1)))
    result.update(max_initial_drift_px=initial_drift, max_target_motion_px=target_motion,
                  hand_motion_px=hand_motion, min_target_hand_distance_px=separation)
    if holding_evidence(initial_pixel, target_pixels, hand_pixels)["status"] == "holding_supported":
        result["reason"] = "positive_holding_evidence"
    elif initial_drift <= 3 and target_motion <= 2 and hand_motion >= 4 and separation >= 12:
        result.update(status="target_not_following_supported",
                      reason="visible_stationary_target_during_separated_hand_probe")
    else:
        result["reason"] = "stationarity_or_probe_separation_not_supported"
    return result
