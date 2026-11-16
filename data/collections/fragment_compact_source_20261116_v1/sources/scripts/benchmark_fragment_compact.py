"""Run the identical batch replay harness with compact exact certificates.

Only the fresh-domain factory and archived sources are injected into the shared
harness. Its source order, cap, clocks, cache rules and target loops are reused.
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import shutil
from time import perf_counter

from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.fragments import Atom
from scripts import benchmark_fragment_batch as replay

_archive_batch_sources = replay.own_sources


def own_sources(output):
    hashes = _archive_batch_sources(output)
    for p in (Path(__file__).resolve().relative_to(Path.cwd()),
              Path("ch3/revision/compact_fragment_certificates.py"),
              Path("tests/test_compact_fragment_certificates.py"),
              Path("docs/fragment_compact_protocol_20261116.md")):
        hashes[str(p)] = replay.digest(p)
        dest = output / "sources" / p
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)
    return hashes


def equivalence(output):
    replay.check(replay.OLD_SOURCE)
    replay.check(replay.FIXTURES)
    output.mkdir(parents=True, exist_ok=False)
    hashes = own_sources(output)
    d = build_compact_domain()
    oracle = d.old.Oracle()
    records = []
    began = perf_counter()
    for expected in replay.read(replay.OLD_SOURCE / "learned_certificates.json"):
        actual = d.regions.certify_region(replay.pattern(expected["pattern"]), oracle)
        assert actual["key"] == expected["key"]
        assert json.dumps(actual["pattern"], sort_keys=True) == json.dumps(expected["pattern"], sort_keys=True)
        assert actual["scope"] == expected["scope"]
        assert len(actual["bindings"]) == len(expected["bindings"])
        checked = 0
        for compact, original in zip(actual["bindings"], expected["bindings"]):
            assert compact["binding"] == original["binding"] and compact["atoms"] == original["atoms"]
            assert compact["bad_state_mask"] == original["bad_state_mask"]
            assert len(compact["minimum_actions"]) == len(d.states)
            atoms = tuple(Atom(**a) for a in compact["atoms"])
            for row in original["states"]:
                index = row["state_index"]
                length = compact["minimum_actions"][index]
                status = "conflict" if length < 0 or length > expected["pattern"]["horizon"] else "ready"
                assert status == row["status"]
                if status == "ready":
                    assert length == len(row["plan"])
                reconstructed = oracle.solve(atoms, d.states[index], expected["pattern"]["horizon"])
                assert reconstructed.status == row["status"]
                assert [asdict(e) for e in reconstructed.plan] == row["plan"]
                checked += 1
        records.append({"key": expected["key"], "state_results_and_full_plans_compared": checked,
                        "all_bindings_distances_statuses_masks_and_plans_consistent": True})
    replay.write(output / "equivalence.json", {"n": len(records), "certificates": records,
                 "state_results": sum(r["state_results_and_full_plans_compared"] for r in records),
                 "audit_wall_ms": (perf_counter() - began) * 1000,
                 "engine_stats_including_out_of_timer_full_plan_audit": oracle.stats(),
                 "unchanged_algorithms": d.unchanged_algorithms,
                 "certification_representation_changed": True})
    replay.close(output, hashes, {str(replay.OLD_SOURCE / "manifest.json"): replay.digest(replay.OLD_SOURCE / "manifest.json")})
    print(json.dumps(replay.read(output / "equivalence.json")), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--phase", choices=("equivalence", "source", "validation"), required=True)
    p.add_argument("--equivalence", type=Path)
    p.add_argument("--training", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    replay.build_batch_domain = build_compact_domain
    replay.own_sources = own_sources
    if args.phase == "equivalence":
        equivalence(args.output)
    elif args.phase == "source" and args.equivalence is not None:
        replay.source(args.equivalence, args.output)
    elif args.phase == "validation" and args.training is not None:
        replay.validation(args.training, args.output)
    else:
        p.error("source requires --equivalence; validation requires --training")


if __name__ == "__main__":
    main()
