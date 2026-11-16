"""Fixed-source signature exclusion audit and paired timing of the main arms."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import random
from statistics import median
from time import perf_counter, process_time

from ch3.context_memory.cases import load_cases
from ch3.revision.fragment_regions import RegionBank
from ch3.revision.history import decide
from scripts.benchmark_revision_fragments import signature
from scripts.benchmark_revision_recovery_pilot import write


def digest(p):
    return sha256(Path(p).read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--training", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    seen_paths = [Path("data/collections/context_memory_development_20261113_v1/cases.json"),
        Path("data/collections/context_memory_validation_20261116_v2/cases.json"),
        Path("data/collections/revision_fragments_20261116_v1/cases.json")]
    seen = {signature(c["snapshot"]) for path in seen_paths for c in load_cases(path)}
    raw_cases = load_cases(args.input/"cases.json")
    excluded = [c["id"] for c in raw_cases if signature(c["snapshot"]) in seen]
    cases = [c for c in raw_cases if signature(c["snapshot"]) not in seen]
    reference = {r["id"]: r["status"] for r in json.loads((args.input/"decisions.json").read_text())}
    assert len(reference) == len(raw_cases)
    train_manifest = json.loads((args.training/"manifest.json").read_text())
    for name, expected in train_manifest["frozen_artifact_hashes"].items():
        assert digest(args.training/name) == expected
    source_manifest = json.loads((args.input/"manifest.json").read_text())
    assert digest(args.input/"cases.json") == source_manifest["frozen_artifact_hashes"]["cases.json"]
    for source, expected in source_manifest["source_hashes"].items():
        assert digest(source) == expected
    banks, loads = {}, {}
    for name in ("expert", "learned"):
        start = perf_counter()
        banks[name] = RegionBank(json.loads((args.training/f"{name}_certificates.json").read_text()))
        loads[name] = (perf_counter()-start)*1000
    start = perf_counter()
    calibration = load_cases(args.training/"calibration_cases.json")
    calibration_load_ms = (perf_counter()-start)*1000
    assert len(calibration) == 256
    write(args.output/"included_ids.json", [c["id"] for c in cases])
    write(args.output/"excluded_ids.json", excluded)
    training = json.loads((args.training/"training.json").read_text())
    protocol = Path("docs/fragment_regions_disjoint_protocol_20261116.md")
    own_sources = [Path(__file__), protocol, Path("ch3/revision/fragment_regions.py"),
                   Path("ch3/revision/fragments.py"), Path("ch3/revision/history.py")]
    hashes = {str(s): digest(s) for s in own_sources}
    write(args.output/"manifest.json", {"input": str(args.input), "training": str(args.training),
        "seen_input_hashes": {str(path): digest(path) for path in seen_paths},
        "source_hashes": hashes, "source_reference_sha256": digest(args.input/"decisions.json"),
        "source_cases_sha256": digest(args.input/"cases.json"),
        "included_ids_sha256": digest(args.output/"included_ids.json"),
        "excluded_ids_sha256": digest(args.output/"excluded_ids.json"),
        "original_n": len(raw_cases), "n": len(cases), "excluded_n": len(excluded),
        "bank_load_ms": loads, "calibration_input_load_ms": calibration_load_ms,
        "training": training, "repeats": 5, "model_calls": 0,
        "audit_addendum_after_whole_grid_results": True})
    records = []
    for repeat in range(5):
        order = list(range(len(cases)))
        random.Random(9073+repeat).shuffle(order)
        for name in (("expert", "learned") if repeat%2 == 0 else ("learned", "expert")):
            statuses, calls, hits = [], 0, 0
            start, cpu_start = perf_counter(), process_time()
            for index in order:
                s = cases[index]["snapshot"]
                hit = banks[name].match(s)
                status = "conflict" if hit else decide(s, s.contract, 8).status
                calls += not hit
                hits += hit
                statuses.append(status)
            wall, cpu = (perf_counter()-start)*1000, (process_time()-cpu_start)*1000
            assert all(status == reference[cases[i]["id"]] for i, status in zip(order, statuses))
            record = {"repeat": repeat, "method": name, "wall_ms": wall, "cpu_ms": cpu,
                "backend_calls": calls, "hits": hits, "correct": len(cases),
                "status_signature": sha256(json.dumps(sorted(
                    (cases[i]["id"], status) for i, status in zip(order, statuses))).encode()).hexdigest()}
            records.append(record)
            write(args.output/"timings.json", records)
            print(json.dumps(record), flush=True)
    summary = {"n": len(cases), "excluded_n": len(excluded), "methods": {}}
    for name in ("expert", "learned"):
        matched = [r for r in records if r["method"] == name]
        preparation = training["expert_preparation_ms" if name == "expert" else "full_preparation_ms"]
        overhead = preparation+loads[name]+(calibration_load_ms if name == "learned" else 0)
        summary["methods"][name] = {"median_wall_ms": median(r["wall_ms"] for r in matched),
            "median_cpu_ms": median(r["cpu_ms"] for r in matched), "build_and_load_ms": overhead,
            "median_including_preparation_ms": overhead+median(r["wall_ms"] for r in matched),
            "backend_calls": matched[0]["backend_calls"], "hits": matched[0]["hits"]}
    write(args.output/"summary.json", summary)
    write(args.output/"closeout.json", {"sources_unchanged": all(digest(s) == h for s, h in hashes.items()),
        "all_statuses_correct": True, "all_repeats_same_signature": len({r["status_signature"] for r in records}) == 1,
        "model_calls": 0})
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
