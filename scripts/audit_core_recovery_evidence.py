"""Read-only audit of the existing same-episode recovery records.

No simulation/model calls and no changes to source experiment records.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def audit(path: Path) -> dict:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    groups = defaultdict(list)
    keys = set()
    for row in rows:
        key = (row["seed"], row["perturbation"], row["method"])
        if key in keys:
            raise ValueError(f"duplicate record: {key}")
        keys.add(key)
        groups[(row["perturbation"], row["method"])].append(row)
    return {
        "source": str(path),
        "records": len(rows),
        "scope": "single_puck_same_episode_simulator_state_feedback",
        "attribution": "OBSERVED_SEARCH is deterministic search, not VLM repair",
        "groups": [
            {
                "perturbation": perturbation,
                "method": method,
                "tasks": len(group),
                "actual_success": sum(bool(r["success"]) for r in group),
                "realized_disturbances": sum(bool(r["perturbation_applied"]) for r in group),
                "reset_counts": sorted({r["reset_count"] for r in group}),
                "vlm_calls": sum(r["vlm_calls"] for r in group),
            }
            for (perturbation, method), group in sorted(groups.items())
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(
        "data/collections/persistent_recovery_protocol_formal_30seed_20260929.jsonl"))
    args = parser.parse_args()
    print(json.dumps(audit(args.source), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
