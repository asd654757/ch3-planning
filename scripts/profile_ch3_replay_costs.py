"""Attribute exclusive costs and measure replay with a preloaded frozen bank."""

import argparse
from collections import Counter
from contextlib import ExitStack
from functools import wraps
import json
from pathlib import Path
import platform
from statistics import median
from time import perf_counter_ns
from unittest.mock import patch

from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.fragment_domain_adapter import IndependentActionBound
from ch3.revision.fragment_purpose_repair import SharedReadyCache
from ch3.schema.model_plan import ModelPlanAction
from scripts.benchmark_architecture_loop import run_loop, semantic_digest
from scripts.benchmark_ch3_fixed_goal_transfer import ARMS
from scripts.benchmark_fragment_purpose_repair import PARTIAL, SOURCE, build_bank
from scripts.benchmark_fragment_three_objects import check, digest, read
from scripts.profile_ch3_failure_lookup import ARCHIVE, measured_bridge


class ExclusiveClock:
    def __init__(self):
        self.ns = Counter()
        self.calls = Counter()
        self.stack = []

    def measure(self, name, function):
        @wraps(function)
        def timed(*args, **kwargs):
            frame = [0]
            self.stack.append(frame)
            start = perf_counter_ns()
            try:
                return function(*args, **kwargs)
            finally:
                elapsed = perf_counter_ns() - start
                self.stack.pop()
                self.ns[name] += elapsed - frame[0]
                self.calls[name] += 1
                if self.stack:
                    self.stack[-1][0] += elapsed

        return timed


def replay(arm, episodes, raw_memo, expected):
    clock = ExclusiveClock()
    start = perf_counter_ns()
    domain = build_compact_domain()
    model = domain.model
    lower = IndependentActionBound(domain)
    bank = clock.measure("bank_setup", build_bank)(domain, arm)
    cache = SharedReadyCache(domain, raw_memo)
    session = domain.Session(bank)
    session.memo = {tuple(r["key"]): (r["status"], tuple(model.Event(**e) for e in r["plan"]))
                    for r in raw_memo}
    session.freeze()
    bridge = measured_bridge(domain, session, lower, clock)
    setup = perf_counter_ns() - start
    phases = {"bank_setup": clock.ns["bank_setup"], "common_setup": setup - clock.ns["bank_setup"]}
    for name in ("accept", "assess", "observe", "issue", "consume"):
        setattr(bridge.Runtime, name, clock.measure(name, getattr(bridge.Runtime, name)))
    extra = (
        patch.object(model, "Clause", clock.measure("clause_construction", model.Clause)),
        patch.object(model.Snapshot, "digest", property(clock.measure("snapshot_hash", model.Snapshot.digest.fget))),
        patch.object(IndependentActionBound, "match", clock.measure("action_lower_bound", IndependentActionBound.match)),
        patch.object(ModelPlanAction, "model_dump", clock.measure("command_serialization", ModelPlanAction.model_dump)),
    )
    runs = {}
    for stage in ("first", "preloaded"):
        previous, previous_calls = clock.ns.copy(), session.backend_calls
        with ExitStack() as scopes:
            for patcher in extra:
                scopes.enter_context(patcher)
            before = perf_counter_ns()
            traces = run_loop(domain, bridge, session, lower, cache, episodes, "no_repair")
            elapsed = perf_counter_ns() - before
        parts = {key: value - previous[key] for key, value in clock.ns.items() if key != "bank_setup"}
        parts["loop_residual"] = elapsed - sum(parts.values())
        assert min(parts.values()) >= 0
        semantic_start = perf_counter_ns()
        trace_hash = semantic_digest(traces)
        audit_ns = perf_counter_ns() - semantic_start
        assert trace_hash == expected["trace_sha256"]
        assert session.backend_calls - previous_calls == expected["methods"][arm]["calls"]
        runs[stage] = {"replay_total": elapsed, "trace_hashing_after_replay": audit_ns, **parts}
    return {"setup": phases, **runs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for source in (ARCHIVE, SOURCE, PARTIAL):
        check(source)
    episodes_path = ARCHIVE / "episodes.json"
    episodes, raw_memo = read(episodes_path), read(SOURCE / "shared_memo.json")
    expected = read(ARCHIVE / "summary.json")
    samples = []
    for repeat in range(8):
        arms = ARMS if repeat < 4 else ARMS[::-1]
        shift = repeat % len(arms)
        for arm in (*arms[shift:], *arms[:shift]):
            samples.append({"arm": arm, "repeat": repeat, **replay(arm, episodes, raw_memo, expected)})
    stages = ("setup", "first", "preloaded")
    medians = {arm: {stage: {part: median(row[stage][part] for row in samples if row["arm"] == arm) / 1e6
                             for part in samples[0][stage]} for stage in stages} for arm in ARMS}
    indexed = {(row["arm"], row["repeat"]): row for row in samples}
    pairs = []
    for repeat in range(8):
        fixed, full = (indexed[arm, repeat] for arm in ("fixed", "learned"))
        setup_delta = sum(full["setup"].values()) - sum(fixed["setup"].values())
        first_saving = fixed["first"]["replay_total"] - full["first"]["replay_total"]
        preloaded_saving = fixed["preloaded"]["replay_total"] - full["preloaded"]["replay_total"]
        pairs.append({"extra_setup_ms": setup_delta / 1e6, "first_replay_saving_ms": first_saving / 1e6,
                      "preloaded_replay_saving_ms": preloaded_saving / 1e6,
                      "cold_total_saving_ms": (first_saving - setup_delta) / 1e6,
                      "on_measured_path_two_batch_saving_ms": (first_saving + preloaded_saving - setup_delta) / 1e6})
    report = {"units": "milliseconds for medians and contrasts, nanoseconds for samples",
              "python": platform.python_version(), "platform": platform.platform(),
              "episode_sha256": digest(episodes_path), "request_count_per_batch": expected["requests"],
              "medians_ms": medians, "learned_vs_fixed_pairs": pairs, "samples": samples}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(medians)


if __name__ == "__main__":
    main()
