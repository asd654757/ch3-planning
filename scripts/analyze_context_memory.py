"""Re-evaluate archives with their OWN frozen selector/core; no API calls."""
import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

from ch3.revision.history import Clause, Contract, Event, Obligation, Snapshot, State
from scripts.benchmark_revision_recovery_pilot import encode, write


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_case(raw):
    s = raw["snapshot"]
    c = s["contract"]
    task = Contract(c["version"], tuple(Obligation(Clause.model_validate(o["clause"]), o["since"]) for o in c["obligations"]))
    return {**raw, "snapshot": Snapshot(task, State(**s["state"]), tuple(Event(**e) for e in s["history"]), s["observation_id"]).validate()}


def canonical(value):
    return json.dumps(value, sort_keys=True, default=encode)


def analyze(path):
    manifest = json.loads((path/"manifest.json").read_text())
    for source, expected in manifest["source_hashes"].items():
        assert digest(path/"sources"/source) == expected, source
    # Frozen core imports these shared semantics; refuse silent reinterpretation.
    for source, expected in manifest["source_hashes"].items():
        if source.startswith(("ch3/revision/", "ch3/schema/")):
            assert digest(source) == expected, source
    assert digest(path/"cases.json") == manifest["cases_sha256"]
    assert digest(path/"memory.json") == manifest["memory_sha256"]
    assert digest(path/"frozen_initial_prompts.json") == manifest["initial_prompts_sha256"]
    spec = importlib.util.spec_from_file_location("context_memory_archive_"+manifest["memory_sha256"][:12],
        path/"sources/ch3/context_memory/core.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    memory = json.loads((path/"memory.json").read_text())
    cases = {c["id"]: c for c in map(load_case, json.loads((path/"cases.json").read_text()))}
    initial = {(r["id"], r["method"]): r for r in json.loads((path/"frozen_initial_prompts.json").read_text())}
    rows = json.loads((path/"results.json").read_text())
    assert len(rows) == len({(r["id"], r["method"]) for r in rows})
    assert {(r["id"], r["method"]) for r in rows} == {tuple(p) for p in manifest["order"]}
    checked, outcomes = 0, {}
    for row in rows:
        case = cases[row["id"]]
        assert row["expected_status"] == case["expected_status"]
        assert row["calls"] == len(row["attempts"]) <= manifest["max_calls_per_pair"]
        assert row["tokens"] == sum(a.get("total_tokens", 0) for a in row["attempts"])
        system, user, selection = module.prompt(case, row["method"], memory)
        assert system == initial[row["id"], row["method"]]["system"] == row["attempts"][0]["system_prompt"]
        assert user == initial[row["id"], row["method"]]["user"] == row["attempts"][0]["user_prompt"]
        assert canonical(selection) == canonical(row["attempts"][0]["selection"])
        final_decision, final_error = None, None
        for attempt in row["attempts"]:
            if "decision" not in attempt:
                continue
            decision, rejection = module.evaluate(case["snapshot"], attempt["content"])
            assert canonical(asdict(decision)) == canonical(attempt["decision"])
            assert canonical(rejection) == canonical(attempt["rejection"])
            assert attempt["status_correct"] == (decision.status == case["expected_status"])
            final_decision, final_error = decision, rejection
            checked += 1
        if row["accepted"]:
            assert final_decision is not None and final_error is None
            assert row["correct"] == (final_decision.status == case["expected_status"])
            assert canonical(module.symbolic_replay(case["snapshot"], final_decision)) == canonical(row["dispatch"])
        else:
            assert not row["correct"]
            assert final_decision is None or final_error is not None
            assert "dispatch" not in row
        first = row["attempts"][0]
        first_correct = bool(first.get("status_correct") and first.get("rejection") is None and "error" not in first)
        assert row["first_correct"] == first_correct
        outcomes[row["id"], row["method"]] = row
    summary = {}
    for method in module.METHODS:
        chosen = [r for r in rows if r["method"] == method]
        summary[method] = {"n": len(chosen), "first_correct": sum(r["first_correct"] for r in chosen),
            "final_correct": sum(r["correct"] and r["accepted"] for r in chosen),
            "feasible_cases": sum(r["expected_status"] == "ready" for r in chosen),
            "symbolic_completions": sum(r.get("dispatch", {}).get("task_complete", False) for r in chosen),
            "calls": sum(r["calls"] for r in chosen),
            "tokens": sum(a.get("total_tokens", 0) for r in chosen for a in r["attempts"]),
            "selected_context_records": sum(r["attempts"][0]["selection"]["selected_records"] for r in chosen),
            "selected_memories": sum(len(r["attempts"][0]["selection"]["selected_memory_ids"]) for r in chosen),
            "unsafe_ready_proposals": sum(a.get("unsafe_ready", False) for r in chosen for a in r["attempts"])}
    identical = []
    for case_id in cases:
        for reference in ("filtered", "filtered_memory"):
            same = initial[case_id, reference]["user"] == initial[case_id, "joint"]["user"]
            if same:
                identical.append({"id": case_id, "reference": reference,
                    "first_outcome_differs": outcomes[case_id, reference]["first_correct"] != outcomes[case_id, "joint"]["first_correct"],
                    "final_outcome_differs": outcomes[case_id, reference]["correct"] != outcomes[case_id, "joint"]["correct"]})
    paired = {}
    for reference in (m for m in module.METHODS if m != "joint"):
        paired[reference] = {}
        for metric in ("first_correct", "correct"):
            counts = Counter()
            for case_id in cases:
                ref = bool(outcomes[case_id, reference][metric])
                joint = bool(outcomes[case_id, "joint"][metric])
                counts["both_correct" if ref and joint else "joint_only" if joint
                       else "reference_only" if ref else "both_failed"] += 1
            paired[reference][metric] = {k: counts[k] for k in
                ("both_correct", "joint_only", "reference_only", "both_failed")}
    failures = [{"id": r["id"], "family": r["family"], "method": r["method"],
        "expected_status": r["expected_status"], "calls": r["calls"],
        "proposed_statuses": [a.get("decision", {}).get("status") for a in r["attempts"]],
        "errors": [a.get("error") for a in r["attempts"]],
        "rejections": [a.get("rejection") for a in r["attempts"]]}
        for r in rows if not (r["correct"] and r["accepted"])]
    return {"input": str(path), "all_archive_hashes_valid": True, "loaded_own_frozen_core": True,
        "first_prompts_and_selection_reproduced": True, "candidate_attempts_replayed": checked,
        "accepted_dispatch_reproduced": True, "summary": summary, "identical_initial_prompt_pairs": identical,
        "paired_joint_vs_reference": paired, "final_failures": failures,
        "attempt_budget_and_token_totals_verified": True,
        "attempts_without_usage": [{"id": r["id"], "method": r["method"],
            "attempt": a["index"], "error": a.get("error")}
            for r in rows for a in r["attempts"] if a.get("total_tokens") is None],
        "raw_results_sha256": digest(path/"results.json"), "raw_data_modified": False,
        "method_identifiability": "joint_context_equals_dependency_context; remaining distinction is experience gating"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--inputs", nargs="+", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    result = [analyze(path) for path in args.inputs]
    write(args.output/"analysis.json", result)
    write(args.output/"manifest.json", {"additional_model_calls": 0, "analysis_source_sha256": digest(__file__),
        "total_pairs": sum(sum(m["n"] for m in r["summary"].values()) for r in result),
        "total_calls": sum(sum(m["calls"] for m in r["summary"].values()) for r in result),
        "total_tokens": sum(sum(m["tokens"] for m in r["summary"].values()) for r in result),
        "attempts_without_usage": sum(len(r["attempts_without_usage"]) for r in result),
        "token_scope": "provider_reported_usage_only; missing response usage is unknown, not proven zero"})
    for report in result:
        print(json.dumps({"input": report["input"], "replayed": report["candidate_attempts_replayed"], "summary": report["summary"]}))


if __name__ == "__main__":
    main()
