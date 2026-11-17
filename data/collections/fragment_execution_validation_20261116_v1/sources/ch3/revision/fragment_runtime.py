"""Frozen conflict fragments inside the existing evidence-bound runtime.

This adapter dispatches registered symbolic actions. It does not implement a
sensor, motion controller, or physical success test. External decisions retain
the original runtime's validation; only the internal planner uses certificates.
"""
from dataclasses import replace
from time import perf_counter
from types import SimpleNamespace

from ch3.revision import evaluation, runtime
from ch3.revision.fragment_domain_adapter import _class, _functions


def build_fragment_runtime(domain, session, lower):
    """Bind an isolated, frozen planner to unchanged runtime methods.

    Both controls receive suffix reuse, the independent action bound, exact
    source cache, and ready-plan validation. No target-time learning occurs.
    Non-ready certificate results enter through the internal planner rather
    than being submitted as untrusted external claims that require re-solving.
    """
    if session.learning or session.cache_writable:
        raise ValueError("execution requires a frozen fragment session")
    if lower.domain is not domain:
        raise ValueError("lower bound belongs to another domain")
    model = domain.model
    evaluation_globals = dict(vars(evaluation))
    for name in ("Contract", "decide", "monitor", "possible_histories", "semantic_key", "successors"):
        evaluation_globals[name] = getattr(model, name)
    _functions(evaluation, evaluation_globals)
    valid_suffix = evaluation_globals["valid_suffix"]
    namespace = dict(vars(runtime))
    for name in ("Decision", "Event", "Snapshot", "State", "decide", "successors"):
        namespace[name] = getattr(model, name)
    namespace["valid_suffix"] = valid_suffix
    namespace["OBJECT_IDS"] = {o: f"{o}_candidate" for o in model.OBJECTS}
    _functions(runtime, namespace)
    records = []

    def routed_decision(snapshot, contract, old_suffix=(), horizon=8):
        started = perf_counter()
        if session.learning or session.cache_writable or horizon != session.bank.horizon:
            raise ValueError("frozen session or horizon changed")
        proposed = replace(snapshot, contract=contract).validate()
        # Also check runtime semantics before suffix reuse or cache access.
        if session.bank._token != domain.regions.runtime_token():
            raise ValueError("executing domain changed: recertify")
        candidate = namespace["rebase_suffix"](proposed, old_suffix)
        proof = None
        if len(candidate) <= horizon and valid_suffix(proposed, contract, candidate):
            origin, status, plan = "reuse_valid_suffix", "ready", candidate
        elif lower.match(proposed, horizon):
            origin, status, plan = "independent_action_bound", "conflict", ()
            proof = {"minimum_actions_lower_bound": lower.value(proposed), "horizon": horizon}
        elif (proof := session.bank.evidence(proposed)) is not None:
            origin, status, plan = "certified_core", "conflict", ()
        else:
            key = domain.Session.resolve.__globals__["exact_key"](proposed)
            if key is not None and key in session.memo:
                status, plan = session.memo[key]
                origin = "exact_cache"
            else:
                result = model.decide(proposed, contract, horizon)
                session.backend_calls += 1
                records.append({"origin": "backend", "status": result.status,
                                "cursor": proposed.cursor, "proof": None,
                                "elapsed_ms": (perf_counter()-started)*1000})
                return result, "FRAGMENT_BACKEND"
        if status == "ready":
            if len(plan) > horizon or not valid_suffix(proposed, contract, plan):
                raise ValueError("invalid cached or reused executable suffix")
        elif status != "conflict" or plan:
            raise ValueError("static cache contains unsupported decision")
        histories = tuple(model.possible_histories(proposed.history))
        violated = () if status == "ready" else tuple(sorted(set().union(
            *(model.monitor(contract, proposed.state, h)[0] for h in histories))))
        result = model.Decision(status, plan, len(histories), len(histories) if status == "ready" else 0,
            horizon, () if status == "ready" else tuple(e.seq for e in proposed.history if e.evidence == "unknown"),
            violated)
        records.append({"origin": origin, "status": status, "cursor": proposed.cursor,
                        "proof": proof, "elapsed_ms": (perf_counter()-started)*1000})
        return result, "FRAGMENT_" + origin.upper()

    namespace["routed_decision"] = routed_decision
    bound_runtime = _class(runtime.RevisionRuntime, namespace)
    return SimpleNamespace(Runtime=bound_runtime, observed_state=namespace["observed_state"],
        valid_suffix=valid_suffix, records=records,
        unchanged_runtime_methods=all(getattr(bound_runtime, name).__code__ is getattr(runtime.RevisionRuntime, name).__code__
            for name in ("__init__", "accept", "observe", "assess", "issue", "consume")))
