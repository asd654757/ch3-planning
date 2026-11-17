"""Post-hoc coverage audit using the already frozen partial-nogood controls.

Not an additional timing arm or a preregistered performance result.
"""
from collections import Counter
from dataclasses import replace
from pathlib import Path
import json

from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.fragment_domain_adapter import IndependentActionBound
from ch3.revision.fragment_partial_nogoods import PartialNogoodBank
from ch3.revision.fragment_repair import candidates, editable_ids
from ch3.revision.fragment_repair_frozen import propose_frozen
from scripts.analyze_fragment_execution import decode
from scripts.benchmark_fragment_repair import SOURCE, cases, signature, validate_plan
from scripts.benchmark_fragment_repair_frozen import FIXTURES, STATIC
from scripts.benchmark_fragment_three_objects import ROOT, check, close, digest, read, write


def main():
    target = ROOT / "fragment_repair_frozen_20261117_v1"
    prior = ROOT / "fragment_partial_nogood_comparison_20261117_v1"
    output = ROOT / "fragment_repair_partial_control_20261117_v1"
    for folder in (target, prior, SOURCE, FIXTURES, STATIC):
        check(folder)
    output.mkdir(parents=True, exist_ok=False)
    labels = read(STATIC / "reference.json")
    suites = {"execution": cases(), "static": [dict(r, stage="static_task")
        for r in read(FIXTURES / "target_cases.json") if labels[r["id"]] != "ready"]}
    d = build_compact_domain()
    fixed = d.regions.RegionBank(read(SOURCE / "fixed_certificates.json"))
    failures = read(prior / "partial_source_source_failures.json")
    source = PartialNogoodBank(d, fixed, failures, mode="source")
    complete = PartialNogoodBank(d, fixed, failures, mode="complete")
    full = d.regions.RegionBank(read(SOURCE / "learned_certificates.json"))
    assert complete.conditions == full.conditions
    lower = IndependentActionBound(d)
    results = {}
    for suite, raw_cases in suites.items():
        gold = read(target / f"{suite}_reference.json")
        stats, coverage = Counter(), Counter()
        records = []
        for raw in raw_cases:
            s = decode(d, raw["snapshot"])
            proposal = propose_frozen(d, s, source, lower, "transfer_recheck", known_nonready=True)
            assert list(signature(proposal)[:1]) + [list(proposal.deleted), proposal.cost] == gold[raw["id"]]
            if proposal.status == "proposal":
                validate_plan(d, s, proposal.contract, proposal.plan)
            stats.update(proposal.stats)
            records.append({"id": raw["id"], "stage": raw["stage"], "result": proposal})
            editable = editable_ids(s)
            for _, ids, task in candidates(d, s, editable, {i: 1 for i in editable}):
                current = replace(s, contract=task)
                full_hit = full.evidence(current) is not None
                source_hit = source.evidence(current) is not None
                assert not source_hit or full_hit
                coverage["authorized_subsets"] += 1
                coverage["full_only_" + raw["stage"]] += full_hit and not source_hit
        write(output / f"{suite}_source_partial_proposals.json", records)
        results[suite] = {"stats": dict(stats), "coverage": dict(coverage)}
    metrics = {"posthoc_coverage_only": True, "complete_nogood_conditions_equal_full_at_all_34_states": True,
        "source_partial_all_repair_answers_and_plans_correct": True, "suites": results}
    write(output / "metrics.json", metrics)
    tracked = [Path(__file__).resolve().relative_to(Path.cwd()),
        Path("ch3/revision/fragment_repair_frozen.py"), Path("ch3/revision/fragment_repair.py"),
        Path("ch3/revision/fragment_partial_nogoods.py")]
    close(output, {str(p): digest(p) for p in tracked},
        {str(f / "manifest.json"): digest(f / "manifest.json") for f in (target, prior, SOURCE, FIXTURES, STATIC)})
    print(json.dumps(metrics), flush=True)


if __name__ == "__main__":
    main()
