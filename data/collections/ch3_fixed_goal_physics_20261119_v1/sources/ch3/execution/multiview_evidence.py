"""Evaluate each holding view independently, with unchanged single-view rules."""
from copy import deepcopy
from ch3.execution.visual_holding import holding_evidence


def holding_from_views(initial_pixels, records):
    cameras = sorted(initial_pixels)
    evidence = {}
    for camera in cameras:
        samples = [r['views'][camera] for r in records]
        evidence[camera] = holding_evidence(initial_pixels[camera],
            [s['target_pixel'] for s in samples], [s['hand_pixel'] for s in samples])
    supported = [c for c in cameras if evidence[c]['status'] == 'holding_supported']
    return dict(status='holding_supported' if supported else 'unknown',
        reason='independent_view_comotion' if supported else 'no_view_supports_holding',
        supporting_cameras=supported, per_camera=evidence,
        raw_initial_pixels=deepcopy(initial_pixels), raw_samples=deepcopy(records),
        limitation='Positive restricted RGB co-motion, not contact or force verification.')
