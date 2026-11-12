"""Re-score archived outputs without further model calls; preserve raw results."""
import argparse
from dataclasses import asdict, is_dataclass
import hashlib
import json
from pathlib import Path
import shutil

from ch3.revision.cases import json_default
from ch3.revision.diagnosis import diagnose
from ch3.revision.evaluation import mechanism_ablation, score
from ch3.revision.history import Clause, Contract, Event, Obligation, Snapshot, State, decide
from ch3.revision.model import parse_output
from scripts.benchmark_history_revision import summarize


def contract(raw):
    return Contract(raw["version"], tuple(Obligation(Clause.model_validate(o["clause"]), o["since"])
                                          for o in raw["obligations"]))


def load_case(raw):
    s = raw["snapshot"]
    return {**raw, "gold": contract(raw["gold"]), "snapshot": Snapshot(contract(s["contract"]),
        State(**s["state"]), tuple(Event(**e) for e in s["history"]), s["observation_id"]).validate()}


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=lambda x:
        asdict(x) if is_dataclass(x) else json_default(x))+"\n")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    sources = [Path(__file__).resolve().relative_to(Path.cwd()),
               *sorted(Path("ch3/revision").glob("*.py")), Path("scripts/benchmark_history_revision.py")]
    hashes = {}
    for source in sources:
        hashes[str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
        dest = args.output/"sources"/source
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    cases = [load_case(c) for c in json.loads((args.input/"cases.json").read_text())]
    by_id = {c["id"]: c for c in cases}
    raw_rows = json.loads((args.input/"results.json").read_text())
    manifest = json.loads((args.input/"manifest.json").read_text())
    expected = {(id, method) for id, method in manifest["call_order"]}
    actual = [(r["id"], r["method"]) for r in raw_rows]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("missing, duplicate or unexpected attempts")
    rows = []
    for raw in raw_rows:
        row = {k: v for k, v in raw.items() if k not in ("score", "proposed", "decision")}
        case = by_id[row["id"]]
        if "content" in row:
            try:
                proposed = parse_output(case, row["method"], row["content"])
                d = decide(case["snapshot"], proposed)
                row.update(score=score(case, proposed, d), decision=asdict(d))
            except Exception as exc:
                row["reanalysis_error"] = type(exc).__name__
        rows.append(row)
    write(args.output/"results.json", rows)
    write(args.output/"summary.json", summarize(rows))
    write(args.output/"ablations.json", [mechanism_ablation(c) for c in cases])
    write(args.output/"diagnostics.json", [{"id": c["id"], "family": c["family"],
        **diagnose(c["snapshot"], c["gold"])} for c in cases])
    write(args.output/"manifest.json", {"input": str(args.input), "additional_model_calls": 0,
        "source_hashes": hashes, "input_results_sha256": hashlib.sha256((args.input/"results.json").read_bytes()).hexdigest(),
        "correction": "unaffected-clause denominator excludes transitive dependency impact; normalized-contract/status/plan metrics unchanged",
        "normalization": "ID independent clause multiset, not full logical equivalence"})
    print(json.dumps(summarize(rows)))


if __name__ == "__main__":
    main()
