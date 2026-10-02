"""Offline RGB evidence audit; never executes, replans, or reads object truth."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image

from ch3.execution.visual_holding import color_pixel, failed_pick_evidence


def pixel(path):
    # Saved display images are flipped in both dimensions relative to calibration.
    frame = np.flip(np.asarray(Image.open(path).convert("RGB")), (0, 1))
    try:
        return color_pixel(frame, "blue")[0]
    except ValueError:
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    rows = []
    for directory in args.sources:
        summary = json.loads((directory / "summary.json").read_text())
        if not summary.get("observation_history"):
            rows.append({"source": str(directory), "status": "not_evaluable",
                         "reason": "no_observation_history"})
            continue
        initial = pixel(directory / "before.png")
        for round_record in summary["observation_history"]:
            round_id = round_record["round"]
            paths = [directory / f"feedback_round{round_id}_{i}.png" for i in range(len(round_record["evidence_frames"]))]
            targets = [pixel(p) for p in paths]
            hands = [f["hand_pixel"] for f in round_record["evidence_frames"]]
            rows.append({"source": str(directory), "round": round_id,
                         "evidence_source": "saved_rgb_and_recorded_hand_proprioception",
                         "images": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
                         "initial_pixel": initial, "target_pixels": targets, "hand_pixels": hands,
                         "decision": failed_pick_evidence(initial, targets, hands)})
    # Exclusive creation prevents overwriting prior diagnostic versions.
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump({"scope": "offline_diagnostic_not_recovery_or_empty_hand_estimation", "records": rows}, stream, indent=2)
    print(json.dumps([{"source": r["source"], "status": r.get("decision", {}).get("status", r.get("status"))} for r in rows]))


if __name__ == "__main__":
    main()
