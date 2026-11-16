"""Mine old verified rejections, then audit finite transfer without model calls."""
import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from itertools import combinations
import json
from pathlib import Path
import shutil
from time import perf_counter

from ch3.context_memory.cases import load_cases
from ch3.context_memory.core import evaluate
from ch3.revision.fragments import (Atom, Oracle, VerifiedBank, contract, eligible_atoms,
    extract, lift, states, structural_conflict)
from ch3.revision.history import Clause, Contract, Event, Obligation, Snapshot, State, decide
from scripts.benchmark_revision_recovery_pilot import write


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def signature(snapshot):
    return repr((tuple(sorted(eligible_atoms(snapshot))), snapshot.state))


def old_failures(path):
    """Only archived rejected candidates trigger mining; labels are not read."""
    cases = {c["id"]: c["snapshot"] for c in load_cases(path/"cases.json")}
    found = {}
    for row in json.loads((path/"results.json").read_text()):
        for attempt in row["attempts"]:
            if attempt.get("rejection") is None:
                continue
            snapshot = cases[row["id"]]
            _, rejection = evaluate(snapshot, attempt["content"])
            if rejection != attempt["rejection"]:
                raise ValueError("old candidate rejection did not reproduce")
            item = found.setdefault(row["id"], {"snapshot": snapshot, "sources": []})
            item["sources"].append({"path": str(path/"results.json"),
                "results_sha256": digest(path/"results.json"), "case_id": row["id"],
                "method": row["method"], "attempt": attempt["index"],
                "content_sha256": sha256(attempt["content"].encode()).hexdigest()})
    return found


def finite_grid(excluded):
    pool = [Atom(k, obj, target) for k in ("at", "visit") for obj in ("blue", "yellow")
            for target in ("table", "green", "return")]
    pool += [Atom("forbid", obj) for obj in ("blue", "yellow")]+[Atom("empty")]
    cases, skipped = [], 0
    for size in range(4):
        for atoms in combinations(pool, size):
            for state in states():
                snapshot = Snapshot(contract(atoms), state, (), "new-finite-grid").validate()
                if signature(snapshot) in excluded:
                    skipped += 1
                    continue
                cases.append({"id": f"grid-{len(cases):05}", "group": "new_static_combinations",
                              "snapshot": snapshot})
    return cases, skipped


def temporal_probes():
    cases = []
    for actor, blocker in (("blue", "yellow"), ("yellow", "blue")):
        for target in ("green", "return"):
            for evidence in ("confirmed", "unknown", "refuted"):
                for anchor in (0, 2):
                    for protected in (False, True):
                        history = (Event(1, "pick", actor, None), Event(2, "place", actor, target, evidence),
                            Event(3, "pick", actor, None), Event(4, "place", actor, "table"),
                            Event(5, "pick", blocker, None), Event(6, "place", blocker, target))
                        obligations = [Obligation(Clause(id="v", kind="visit", object=actor, target=target), anchor)]
                        if protected:
                            obligations.append(Obligation(Clause(id="f", kind="forbid", object=blocker), 6))
                        task = Contract(0, tuple(obligations))
                        state = State(**{actor: "table", blocker: target})
                        snapshot = Snapshot(task, state, history, "new-temporal-probe").validate()
                        cases.append({"id": f"temporal-{len(cases):03}", "group": "temporal_counterexamples",
                                      "snapshot": snapshot})
    return cases


def solve(snapshot, counter):
    start = perf_counter()
    counter["calls"] += 1
    result = decide(snapshot, snapshot.contract, 8).status
    counter["solver_ms"] += (perf_counter()-start)*1000
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, default=Path("data/collections/context_memory_validation_20261116_v2"))
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    sources = [Path(__file__).resolve().relative_to(Path.cwd()), Path("ch3/revision/fragments.py"),
        Path("ch3/revision/history.py"), Path("ch3/revision/evaluation.py"), Path("ch3/revision/feedback.py"),
        Path("ch3/context_memory/core.py"), Path("ch3/context_memory/cases.py"),
        Path("tests/test_revision_fragments.py"), Path("docs/revision_fragments_protocol_20261116.md")]
    hashes = {}
    for source in sources:
        hashes[str(source)] = digest(source)
        target = args.output/"sources"/source
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    training_start = perf_counter()
    old = old_failures(args.source)
    oracle = Oracle()
    grounded, patterns, mining = {}, {}, []
    for case_id, item in sorted(old.items()):
        produced = []
        for reverse in (False, True):
            core = extract(item["snapshot"], oracle, reverse=reverse)
            if core is not None:
                grounded[core.key] = core
                lifted = lift(core)
                patterns[lifted.key] = lifted
                produced.append({"grounded_key": core.key, "lifted_key": lifted.key,
                                 "deletion_order": "reverse" if reverse else "forward"})
        mining.append({"case_id": case_id, "sources": item["sources"], "produced": produced,
            "outcome": "certifiable_static_relaxation" if produced else "abstain_relaxation_feasible"})
    extraction_calls = oracle.calls
    grounded_bank = VerifiedBank(grounded.values(), oracle)
    grounded_cert_calls = oracle.calls-extraction_calls
    lifted_bank = VerifiedBank(patterns.values(), oracle)
    training_ms = (perf_counter()-training_start)*1000
    grid, excluded = finite_grid({signature(item["snapshot"]) for item in old.values()})
    cases = grid+temporal_probes()
    write(args.output/"mining.json", mining)
    write(args.output/"patterns.json", list(patterns.values()))
    write(args.output/"grounded_patterns.json", list(grounded.values()))
    write(args.output/"certificates.json", lifted_bank.certificates)
    write(args.output/"grounded_certificates.json", grounded_bank.certificates)
    write(args.output/"cases.json", cases)
    write(args.output/"manifest.json", {"created_utc": datetime.now(timezone.utc).isoformat(),
        "model_calls": 0, "physical_execution": False, "source_hashes": hashes,
        "source_cases_sha256": digest(args.source/"cases.json"),
        "source_results_sha256": digest(args.source/"results.json"),
        "cases_sha256": digest(args.output/"cases.json"),
        "patterns_sha256": digest(args.output/"patterns.json"),
        "certificates_sha256": digest(args.output/"certificates.json"),
        "grounded_patterns_sha256": digest(args.output/"grounded_patterns.json"),
        "grounded_certificates_sha256": digest(args.output/"grounded_certificates.json"),
        "source_cases_with_verified_rejections": len(old),
        "source_rejected_candidates": sum(len(item["sources"]) for item in old.values()),
        "mining_oracle_calls": extraction_calls, "grounded_certification_calls": grounded_cert_calls,
        "lifted_certification_calls": oracle.calls-extraction_calls-grounded_cert_calls,
        "total_extraction_certification_calls": oracle.calls, "training_wall_ms": training_ms,
        "grid_source_signatures_excluded": excluded, "test_cases": len(cases),
        "scope": "finite two-object static combinations and temporal boundary probes; not independent robot trials",
        "primary_strong_control": "handwritten domain invariants, same task/state/history",
        "minimality": "inclusion-minimal under two predetermined greedy deletion orders; not minimum cardinality"})
    methods = ("recompute", "grounded_core", "lifted_core", "handwritten_rules")
    counters = {m: {"calls": 0, "solver_ms": 0., "wall_ms": 0., "hits": 0,
        "false_conflicts": 0, "status_matches": 0} for m in methods}
    rows = []
    for i, case in enumerate(cases):
        snapshot = case["snapshot"]
        started = perf_counter()
        expected = solve(snapshot, counters["recompute"])
        counters["recompute"]["wall_ms"] += (perf_counter()-started)*1000
        counters["recompute"]["status_matches"] += 1
        result = {"id": case["id"], "group": case["group"], "reference_status": expected,
                  "methods": {"recompute": {"status": expected, "hit": False}}}
        for method in methods[1:]:
            started = perf_counter()
            hit = (grounded_bank.match(snapshot) if method == "grounded_core" else
                   lifted_bank.match(snapshot) if method == "lifted_core" else structural_conflict(snapshot))
            status = "conflict" if hit else solve(snapshot, counters[method])
            counters[method]["wall_ms"] += (perf_counter()-started)*1000
            counters[method]["hits"] += bool(hit)
            counters[method]["false_conflicts"] += bool(hit and expected != "conflict")
            counters[method]["status_matches"] += status == expected
            result["methods"][method] = {"status": status, "hit": bool(hit),
                "pattern": hit.get("pattern") if isinstance(hit, dict) else None,
                "binding": hit.get("binding") if isinstance(hit, dict) else None}
        rows.append(result)
        if (i+1)%1000 == 0:
            write(args.output/"results.json", rows)
            print(json.dumps({"completed": i+1, "total": len(cases)}), flush=True)
    write(args.output/"results.json", rows)
    write(args.output/"summary.json", {"n": len(rows), "methods": counters,
        "reference_statuses": Counter(r["reference_status"] for r in rows),
        "by_group": {g: {"n": sum(r["group"] == g for r in rows),
            **{m: {"hits": sum(r["methods"][m]["hit"] for r in rows if r["group"] == g),
                   "false_conflicts": sum(r["methods"][m]["hit"] and r["reference_status"] != "conflict"
                       for r in rows if r["group"] == g)} for m in methods}}
            for g in sorted({r["group"] for r in rows})}})
    manifest = json.loads((args.output/"manifest.json").read_text())
    frozen_files = {k.removesuffix("_sha256")+".json": manifest[k] for k in
        ("cases_sha256", "patterns_sha256", "certificates_sha256", "grounded_patterns_sha256", "grounded_certificates_sha256")}
    write(args.output/"closeout.json", {"sources_unchanged": all(digest(p) == h for p, h in hashes.items()),
        "frozen_files_unchanged": all(digest(args.output/p) == h for p, h in frozen_files.items()),
        "source_archive_unchanged": digest(args.source/"results.json") == manifest["source_results_sha256"]
            and digest(args.source/"cases.json") == manifest["source_cases_sha256"],
        "model_calls": 0, "patterns": len(patterns), "grounded_cores": len(grounded),
        "cases": len(rows), "all_statuses_match": all(c["status_matches"] == len(rows) for c in counters.values())})
    print(json.dumps({"n": len(rows), "patterns": len(patterns), "methods": counters}), flush=True)


if __name__ == "__main__":
    main()
