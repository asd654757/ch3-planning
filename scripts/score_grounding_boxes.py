"""Evaluator-only localization coverage; no semantic identity certification."""

import argparse
import itertools
import json
from pathlib import Path


def iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0.0


def score(objects, truth, width, height):
    if len(objects) > 8 or len(truth) > 8:
        raise ValueError("smoke matching supports at most eight objects")
    predicted = [[o["box"][key] / 1000 for key in ("x_min", "y_min", "x_max", "y_max")] for o in objects]
    expected = [(name, [box[0] / width, box[1] / height, box[2] / width, box[3] / height])
                for name, item in truth.items() if (box := item["bbox"]) is not None]
    # Small-scene exhaustive one-to-one matching, with unmatched predictions.
    best, assignments = -1, []
    for mapping in itertools.product(range(-1, len(expected)), repeat=len(predicted)):
        used = [x for x in mapping if x >= 0]
        if len(used) != len(set(used)):
            continue
        value = sum(iou(predicted[i], expected[j][1]) for i, j in enumerate(mapping) if j >= 0)
        if value > best:
            best = value
            assignments = mapping
    matches = [{"object_id": objects[i]["object_id"], "truth_handle": expected[j][0] if j >= 0 else None,
                "iou": iou(predicted[i], expected[j][1]) if j >= 0 else 0.0}
               for i, j in enumerate(assignments)]
    return {"predicted_count": len(predicted), "visible_truth_count": len(expected),
            "matches": matches, "matched_at_iou_0_5": sum(m["iou"] >= 0.5 for m in matches),
            "scope": "geometric coverage only; not semantic accuracy or executable binding"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--truth", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    from PIL import Image
    truth = json.loads(Path(args.truth).read_text(encoding="utf-8"))
    rows = []
    for line in Path(args.predictions).read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        with Image.open(record["image_path"]) as image:
            width, height = image.size
        result = score(record.get("localization", {}).get("objects", []), truth, width, height)
        rows.append({"case_id": record["case_id"], **result})
    with Path(args.output).open("x", encoding="utf-8") as handle:
        json.dump(rows, handle, indent=2)
    print(json.dumps(rows), flush=True)


if __name__ == "__main__":
    main()
