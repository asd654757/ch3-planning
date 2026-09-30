"""Image/instruction grounding only; no hidden truth or robot execution."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from ch3.vlm.client import DashScopeVLMClient
from ch3.vlm.scene_grounding import GroundingParseError, SceneGrounder


PUBLIC_FIELDS = {"case_id", "instruction", "image_path", "seed"}


def load_manifest(path: Path) -> list[dict]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("manifest must be a nonempty list")
    seen = set()
    for row in rows:
        if set(row) != PUBLIC_FIELDS:
            raise ValueError("manifest must contain only public fields; keep truth separate")
        if not isinstance(row["case_id"], str) or row["case_id"] in seen:
            raise ValueError("invalid or duplicate case_id")
        seen.add(row["case_id"])
        if not isinstance(row["instruction"], str) or not row["instruction"].strip():
            raise ValueError("missing instruction")
        if not isinstance(row["seed"], int) or isinstance(row["seed"], bool):
            raise ValueError("seed must be an integer")
        image = Path(row["image_path"])
        if not image.is_absolute():
            image = path.parent / image
        if not image.is_file():
            raise ValueError(f"missing image for {row['case_id']}")
        row["image_path"] = str(image.resolve())
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--env-file")
    parser.add_argument("--model", default="qwen3-vl-flash")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    rows = load_manifest(Path(args.manifest))
    if args.check_only:
        print(json.dumps({"manifest_valid": True, "cases": len(rows), "model_calls": 0}))
        return
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents an accidental rerun from destroying evidence.
    with output.open("x", encoding="utf-8") as handle:
        client = DashScopeVLMClient(model=args.model, env_path=args.env_file)
        grounder = SceneGrounder(client)
        accepted = 0
        for row in rows:
            print(f"[grounding] case={row['case_id']} start", flush=True)
            record = {"record_type": "unstructured_grounding_case", **row,
                      "image_sha256": hashlib.sha256(Path(row["image_path"]).read_bytes()).hexdigest(),
                      "accepted_for_planning": False, "execution_attempted": False}
            try:
                scene, response = grounder.ground(
                    instruction=row["instruction"], image_path=row["image_path"],
                    skills=["pick", "place", "push", "press"], arms=["right"], seed=row["seed"],
                )
            except GroundingParseError as exc:
                record.update({"error": str(exc), "raw_response": exc.response.content,
                               "model": exc.response.model, "total_tokens": exc.response.total_tokens,
                               "latency_ms": exc.response.latency_ms})
            except Exception as exc:
                record["error"] = str(exc)
            else:
                record.update({"grounding": scene.model_dump(mode="json"),
                               "raw_response": response.content, "model": response.model,
                               "total_tokens": response.total_tokens, "latency_ms": response.latency_ms})
                try:
                    state, goal = scene.to_planning_input(arms={"right"})
                    bindings = scene.visual_bindings()
                    record.update({"accepted_for_planning": True,
                                   "estimated_facts": sorted(state.facts() | state.empty_hand_facts({"right"})),
                                   "parsed_goal": goal.facts, "image_regions": bindings})
                    accepted += 1
                except ValueError as exc:
                    record["reject_reason"] = str(exc)
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            print(json.dumps({"case_id": row["case_id"], "accepted_for_planning": record["accepted_for_planning"]}), flush=True)
        summary = {"record_type": "unstructured_grounding_summary", "cases": len(rows),
                   "accepted_for_planning": accepted, "execution_attempted": 0,
                   "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                   "scope": "front-end feasibility; acceptance is not perception accuracy or task success"}
        handle.write(json.dumps(summary) + "\n")
        print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
