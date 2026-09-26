#!/usr/bin/env python3
"""Field-by-field diff of two ``symbolic_planner_cases.jsonl`` files.

Used for the held-object guard ablation: the planner is deterministic now, so a
difference between two runs can only come from the code that changed.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def load(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("record_type") != "symbolic_planner_case":
            continue
        out[(str(row["protocol"]), str(row["mode"]), json.dumps(row["point_key"]))] = row
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a", required=True)
    parser.add_argument("--b", required=True)
    parser.add_argument(
        "--field",
        action="append",
        default=None,
        help="case fields to compare (default: the planner's own outputs)",
    )
    args = parser.parse_args()

    fields = args.field or [
        "suffix_actions",
        "search_success",
        "search_reason",
        "symbolic_valid",
        "goal_satisfied",
        "observed_state_facts",
        "goal_holds_in_observed_state",
    ]
    a, b = load(Path(args.a)), load(Path(args.b))
    keys = sorted(set(a) & set(b))
    differ = {
        field: [
            key
            for key in keys
            if json.dumps(a[key].get(field), sort_keys=True)
            != json.dumps(b[key].get(field), sort_keys=True)
        ]
        for field in fields
    }
    print(
        json.dumps(
            {
                "a": args.a,
                "b": args.b,
                "keys_a": len(a),
                "keys_b": len(b),
                "keys_shared": len(keys),
                "only_in_a": len(set(a) - set(b)),
                "only_in_b": len(set(b) - set(a)),
                "identical_all_fields": sum(
                    1 for key in keys if all(not differ[f] or key not in differ[f] for f in fields)
                ),
                "differ_by_field": {
                    field: {"count": len(items), "examples": items[:5]}
                    for field, items in differ.items()
                },
                "differ_by_mode": dict(
                    Counter(key[1] for field in fields for key in differ[field])
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
