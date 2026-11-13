"""Independent archive replay; correct conditional metrics without editing raw data."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil

from ch3.revision.recovery_pilot import candidate, prompt
from ch3.revision.history import Event
from scripts.analyze_history_revision import load_case
from scripts.benchmark_revision_recovery_pilot import encode, summarize, symbolic_dispatch, write


def canonical(value):
    return json.dumps(value, sort_keys=True, default=encode)


def analyze(path):
    manifest = json.loads((path/"manifest.json").read_text())
    assert hashlib.sha256((path/"cases.json").read_bytes()).hexdigest() == manifest["cases_sha256"]
    for source, expected in manifest["source_hashes"].items():
        assert hashlib.sha256((path/"sources"/source).read_bytes()).hexdigest() == expected
    # Replay uses identical semantics/translation/runtime code, not a new solver.
    for name in ("history.py", "model.py", "evaluation.py", "recovery_pilot.py", "runtime.py", "cases.py"):
        source = "ch3/revision/"+name
        assert hashlib.sha256(Path(source).read_bytes()).hexdigest() == manifest["source_hashes"][source]
    cases = {}
    for raw in json.loads((path/"cases.json").read_text()):
        case = load_case(raw)
        case["old_suffix"] = tuple(Event(**e) for e in raw["old_suffix"])
        cases[case["id"]] = case
    rows = json.loads((path/"results.json").read_text())
    expected = {tuple(t) for t in manifest["order"]}
    actual = [(r["id"], r["method"]) for r in rows]
    assert len(actual) == len(set(actual)) and set(actual) == expected
    differences, checked_attempts = [], 0
    for row in rows:
        case = cases[row["id"]]
        assert prompt(case, row["method"]) == (row["attempts"][0]["system_prompt"], row["attempts"][0]["user_prompt"])
        for attempt in row["attempts"]:
            if "score" not in attempt:
                continue
            proposed, decision, route, error = candidate(case, row["method"], attempt["content"])
            from ch3.revision.evaluation import score
            for key, value in (("proposed", proposed), ("decision", decision), ("route", route),
                               ("rejection", error), ("score", score(case, proposed, decision))):
                assert canonical(value) == canonical(attempt[key]), (row["id"], key)
            attempt["decision"] = decision
            checked_attempts += 1
        if row["accepted"]:
            attempt = row["attempts"][-1]
            proposed, decision, _, error = candidate(case, row["method"], attempt["content"])
            assert error is None
            assert canonical(symbolic_dispatch(case, proposed, decision)) == canonical(row["dispatch"])
        if not row["accepted"] and row["attempts"][-1].get("score", {}).get("semantic_exact"):
            differences.append({"id": row["id"], "method": row["method"],
                                "reason": "correct_translation_but_rejected_decision_or_suffix"})
    return {"input": str(path), "summary": summarize(rows), "checked_attempts": checked_attempts,
        "pairs": len(rows), "all_archive_hashes_valid": True, "all_semantic_sources_match": True,
        "all_first_prompts_match_without_labels": True, "all_accepted_dispatch_replays_match": True,
        "conditional_summary_corrections": differences,
        "calls": sum(r["calls"] for r in rows), "tokens": sum(r["tokens"] for r in rows),
        "input_results_sha256": hashlib.sha256((path/"results.json").read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    reports = [analyze(path) for path in args.inputs]
    sources = [Path(__file__).resolve().relative_to(Path.cwd()),
               Path("scripts/analyze_history_revision.py"), Path("scripts/benchmark_revision_recovery_pilot.py"),
               *sorted(Path("ch3/revision").glob("*.py"))]
    hashes = {}
    for source in sources:
        hashes[str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
        dest = args.output/"sources"/source
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    write(args.output/"analysis.json", reports)
    write(args.output/"manifest.json", {"additional_model_calls": 0, "sources": hashes,
        "raw_data_modified": False, "summary_correction": "score_translation_and_status_on_final_attempt_even_when_suffix_rejected",
        "total_development_calls": sum(r["calls"] for r in reports),
        "total_development_tokens": sum(r["tokens"] for r in reports)})
    for report in reports:
        print(json.dumps({"input": report["input"], "attempts_verified": report["checked_attempts"], "summary": report["summary"]}))


if __name__ == "__main__":
    main()
