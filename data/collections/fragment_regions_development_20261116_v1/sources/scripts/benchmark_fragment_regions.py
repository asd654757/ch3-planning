"""Development and frozen new-combination timing for certified core regions."""
import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from itertools import combinations
import json
from pathlib import Path
import random
import shutil
from statistics import median
from time import perf_counter

from ch3.context_memory.cases import load_cases
from ch3.revision.fragments import Atom, Oracle, Pattern, contract, states
from ch3.revision.fragment_regions import RegionBank, canonical, expert_patterns, minimize
from ch3.revision.history import Snapshot, decide
from scripts.benchmark_revision_fragments import old_failures
from scripts.benchmark_revision_recovery_pilot import write


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def train(calibration, output):
    started = perf_counter()
    oracle = Oracle()
    expert_start = perf_counter()
    expert = RegionBank.verified(expert_patterns(), oracle)
    expert_ms = (perf_counter()-expert_start)*1000
    old = old_failures(Path("data/collections/context_memory_validation_20261116_v2"))
    seeds = {}
    for case_id, item in sorted(old.items()):
        core = minimize(item["snapshot"], oracle)
        if core:
            p = canonical(core)
            seeds[p.key] = p
    seed_bank = RegionBank.verified(seeds.values(), oracle)
    combined_seed = RegionBank.combine(expert, seed_bank)
    learned = {}
    active = combined_seed
    trace = []
    calibration_calls = 0
    for case in calibration:
        snapshot = case["snapshot"]
        hit = active.match(snapshot)
        status = "conflict" if hit else decide(snapshot, snapshot.contract, 8).status
        calibration_calls += not hit
        new_key = None
        if not hit and status == "conflict" and len(learned) < 8:
            core = minimize(snapshot, oracle)
            if core:
                p = canonical(core)
                if p.key not in seeds and p.key not in learned:
                    new_bank = RegionBank.verified([p], oracle)
                    active = RegionBank.combine(active, new_bank)
                    learned[p.key] = p
                    new_key = p.key
        trace.append({"id": case["id"], "snapshot_sha256": snapshot.digest,
            "backend_called": not hit, "status": status, "new_pattern": new_key})
    report = {"full_preparation_ms": (perf_counter()-started)*1000,
        "expert_preparation_ms": expert_ms, "instrumented_core_oracle_calls": oracle.calls,
        "calibration_backend_calls": calibration_calls, "calibration_cases": len(calibration),
        "initial_patterns": len(seeds), "new_calibration_patterns": len(learned),
        "verified_model_rejections": sum(len(i["sources"]) for i in old.values()),
        "scope": "existing finite development cases; backend infeasibility is not a physical execution failure"}
    write(output/"training.json", report)
    write(output/"calibration_trace.json", trace)
    write(output/"seed_patterns.json", list(seeds.values()))
    write(output/"learned_patterns.json", list(learned.values()))
    for name, bank in (("expert", expert), ("seed", combined_seed), ("learned", active)):
        write(output/f"{name}_certificates.json", bank.certificates)
    return {"expert": expert, "seed": combined_seed, "learned": active}, report


def new_cases():
    pool = [Atom(k, obj, target) for k in ("at", "visit") for obj in ("blue", "yellow")
            for target in ("table", "green", "return")]
    pool += [Atom("forbid", obj) for obj in ("blue", "yellow")]+[Atom("empty")]
    cases = []
    for atoms in combinations(pool, 4):
        for state in states():
            snapshot = Snapshot(contract(atoms), state, (), "four-clause-validation").validate()
            cases.append({"id": f"four-{len(cases):05}", "snapshot": snapshot})
    return cases


def timing(cases, banks, repeats, output):
    methods = ("recompute", "expert", "seed", "learned")
    records, first = [], {}
    for repeat in range(repeats):
        indices = list(range(len(cases)))
        random.Random(7361+repeat).shuffle(indices)
        method_order = list(methods[repeat%len(methods):]+methods[:repeat%len(methods)])
        for method in method_order:
            statuses, hits, calls = [], 0, 0
            started = perf_counter()
            for i in indices:
                s = cases[i]["snapshot"]
                hit = method != "recompute" and banks[method].match(s)
                if hit:
                    hits += 1
                    status = "conflict"
                else:
                    calls += 1
                    status = decide(s, s.contract, 8).status
                statuses.append(status)
            elapsed = (perf_counter()-started)*1000
            ordered = [None]*len(cases)
            for i, status in zip(indices, statuses):
                ordered[i] = status
            record = {"repeat": repeat, "method": method, "wall_ms": elapsed,
                      "hits": hits, "backend_calls": calls,
                      "statuses_sha256": sha256(json.dumps(ordered).encode()).hexdigest()}
            records.append(record)
            first.setdefault(method, ordered)
            assert ordered == first[method], "non-deterministic bounded status"
            write(output/"timings.json", records)
            print(json.dumps(record), flush=True)
    assert all(v == first["recompute"] for v in first.values()), "false rejection or mismatched decision"
    write(output/"decisions.json", [{"id": case["id"], "status": status}
                                   for case, status in zip(cases, first["recompute"])])
    summary = {m: {"median_wall_ms": median(r["wall_ms"] for r in records if r["method"] == m),
                   "backend_calls": next(r["backend_calls"] for r in records if r["method"] == m),
                   "hits": next(r["hits"] for r in records if r["method"] == m)} for m in methods}
    write(output/"summary.json", {"n": len(cases), "repeats": repeats, "methods": summary,
        "statuses": Counter(first["recompute"]), "all_methods_status_equal": True,
        "model_calls": 0, "physical_execution": False})
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--phase", choices=("development", "validation"), required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--frozen-training", type=Path)
    p.add_argument("--repeats", type=int, default=5)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    sources = [Path(__file__).resolve().relative_to(Path.cwd()), Path("ch3/revision/fragment_regions.py"),
        Path("ch3/revision/fragments.py"), Path("ch3/revision/history.py"),
        Path("scripts/benchmark_revision_fragments.py"), Path("tests/test_fragment_regions.py")]
    protocol = Path("docs/fragment_regions_protocol_20261116.md")
    if protocol.exists():
        sources.append(protocol)
    source_hashes = {}
    for source in sources:
        source_hashes[str(source)] = digest(source)
        dest = args.output/"sources"/source
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    prior = Path("data/collections/revision_fragments_20261116_v1/cases.json")
    old_cases = load_cases(prior)
    load_times = {}
    if args.phase == "development":
        random.Random(613).shuffle(old_cases)
        calibration = old_cases[:256]
        write(args.output/"calibration_cases.json", calibration)
        banks, training = train(calibration, args.output)
        cases = old_cases
    else:
        if args.frozen_training is None:
            raise ValueError("validation requires previously frozen training")
        prior_manifest = json.loads((args.frozen_training/"manifest.json").read_text())
        for name, expected in prior_manifest["frozen_artifact_hashes"].items():
            if digest(args.frozen_training/name) != expected:
                raise ValueError("frozen training artifact changed")
        banks = {}
        for name in ("expert", "seed", "learned"):
            start = perf_counter()
            banks[name] = RegionBank(json.loads((args.frozen_training/f"{name}_certificates.json").read_text()))
            load_times[name] = (perf_counter()-start)*1000
        training = json.loads((args.frozen_training/"training.json").read_text())
        cases = new_cases()
    write(args.output/"cases.json", cases)
    artifacts = [p.name for p in args.output.glob("*.json")]
    frozen = {name: digest(args.output/name) for name in artifacts}
    manifest = {"created_utc": datetime.now(timezone.utc).isoformat(), "phase": args.phase,
        "source_hashes": source_hashes, "frozen_artifact_hashes": frozen,
        "prior_development_cases_sha256": digest(prior), "repeats": args.repeats,
        "frozen_training": str(args.frozen_training) if args.frozen_training else None,
        "bank_recertification_ms": load_times, "training": training,
        "validation_labels_available_to_training": False,
        "timing": "single-process serial; whole-pass timers; rotating method order; shuffled identical query orders",
        "model_calls": 0, "physical_execution": False}
    write(args.output/"manifest.json", manifest)
    summary = timing(cases, banks, args.repeats, args.output)
    write(args.output/"closeout.json", {"sources_unchanged": all(digest(p) == h for p, h in source_hashes.items()),
        "frozen_artifacts_unchanged": all(digest(args.output/p) == h for p, h in frozen.items()),
        "all_statuses_equal": True, "model_calls": 0})
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
