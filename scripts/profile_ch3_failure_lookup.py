"""Profile the unchanged 240-request replay with phase-level wall clocks."""

import argparse
from collections import Counter
from copy import copy
from functools import wraps
import json
from pathlib import Path
import platform
from statistics import median
from time import perf_counter_ns
from types import SimpleNamespace

from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.fragment_domain_adapter import IndependentActionBound, _function
from ch3.revision.fragment_purpose_repair import SharedReadyCache
from ch3.revision import evaluation, fragment_runtime
from scripts.benchmark_architecture_loop import run_loop, semantic_digest
from scripts.benchmark_ch3_fixed_goal_transfer import ARMS
from scripts.benchmark_fragment_purpose_repair import PARTIAL, SOURCE, build_bank
from scripts.benchmark_fragment_three_objects import check, digest, read


ARCHIVE = Path("data/collections/ch3_fixed_goal_transfer_20261119_v1")


class PhaseClock:
    def __init__(self):
        self.ns = Counter()
        self.calls = Counter()

    def measure(self, name, function):
        @wraps(function)
        def timed(*args, **kwargs):
            start = perf_counter_ns()
            value = function(*args, **kwargs)
            elapsed = perf_counter_ns() - start
            self.ns[name] += elapsed
            self.calls[name] += 1
            if name == "bank_lookup" and value is not None:
                self.calls["bank_hit"] += 1
            return value

        return timed


def measured_bridge(domain, session, lower, clock):
    # Keep the verified domain's originals intact. Bound regions still see the
    # original solver when checking their certification token.
    view = copy(domain)
    view.model = SimpleNamespace(**{**vars(domain.model), "decide": clock.measure("solver", domain.model.decide)})
    lower_view = copy(lower)
    lower_view.domain = view
    session.bank.evidence = clock.measure("bank_lookup", session.bank.evidence)

    def bind(module, namespace):
        fragment_runtime._functions(module, namespace)
        if module is evaluation:
            namespace["valid_suffix"] = clock.measure("suffix_validation", namespace["valid_suffix"])

    setup = fragment_runtime.build_fragment_runtime
    return _function(setup, {**setup.__globals__, "_functions": bind})(view, session, lower_view)


def replay(arm, episodes, raw_memo):
    clock = PhaseClock()
    start = perf_counter_ns()
    domain = build_compact_domain()
    lower = IndependentActionBound(domain)
    bank = clock.measure("bank_load_and_compile", build_bank)(domain, arm)
    cache = SharedReadyCache(domain, raw_memo)
    session = domain.Session(bank)
    session.memo = {tuple(r["key"]): (r["status"], tuple(domain.model.Event(**e) for e in r["plan"]))
                    for r in raw_memo}
    session.freeze()
    bridge = measured_bridge(domain, session, lower, clock)
    prepared = perf_counter_ns()
    traces = run_loop(domain, bridge, session, lower, cache, episodes, "no_repair")
    done = perf_counter_ns()
    assert session.backend_calls == clock.calls["solver"]
    routes = Counter(row["origin"] for row in bridge.records)
    assert routes["certified_core"] == clock.calls["bank_hit"]
    clock.ns["other_setup"] = prepared - start - clock.ns["bank_load_and_compile"]
    clock.ns["other_replay"] = (done - prepared - clock.ns["bank_lookup"]
                                - clock.ns["solver"] - clock.ns["suffix_validation"])
    clock.ns["target_total"] = done - start
    return {"ms": {k: v / 1e6 for k, v in clock.ns.items()}, "calls": dict(clock.calls),
            "routes": dict(routes), "trace_sha256": semantic_digest(traces)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for source in (ARCHIVE, SOURCE, PARTIAL):
        check(source)
    inputs = ARCHIVE / "episodes.json"
    episodes = read(inputs)
    raw_memo = read(SOURCE / "shared_memo.json")
    expected = read(ARCHIVE / "summary.json")
    rows = []
    for repeat in range(8):
        order = ARMS if repeat < 4 else tuple(reversed(ARMS))
        shift = repeat % len(ARMS)
        for arm in (*order[shift:], *order[:shift]):
            sample = replay(arm, episodes, raw_memo)
            assert sample["trace_sha256"] == expected["trace_sha256"]
            assert sample["calls"]["solver"] == expected["methods"][arm]["calls"]
            rows.append({"arm": arm, "repeat": repeat, **sample})
    medians = {arm: {key: median(s["ms"][key] for s in rows if s["arm"] == arm)
                     for key in rows[0]["ms"]} for arm in ARMS}
    by_pair = {(r["arm"], r["repeat"]): r["ms"] for r in rows}
    contrasts = {}
    for arm in ARMS[1:]:
        contrasts[arm] = {key: [by_pair[arm, i][key] - by_pair["standard", i][key] for i in range(8)]
                          for key in medians[arm]}
    report = {"request_count": expected["requests"], "python": platform.python_version(),
              "platform": platform.platform(), "repeat_count": 8, "episode_sha256": digest(inputs),
              "prior_source_preparation_median_ms": expected["prior_source_preparation_median_ms"],
              "medians_ms": medians, "paired_delta_vs_empty_ms": contrasts, "samples": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(medians)


if __name__ == "__main__":
    main()
