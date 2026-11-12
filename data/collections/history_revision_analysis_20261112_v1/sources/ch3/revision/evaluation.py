"""Gold-only scoring and explicit dependency-impact diagnostics."""
from dataclasses import replace

from ch3.revision.history import Contract, decide, monitor, possible_histories, semantic_key, successors


def impact_set(old: Contract, new: Contract):
    previous = {o.clause.id: o for o in old.obligations}
    revised = {o.clause.id: o for o in new.obligations}
    impacted = {k for k in previous.keys() | revised.keys() if previous.get(k) != revised.get(k)}
    # Propagate through BOTH graphs, including deleted references.
    changed = True
    while changed:
        changed = False
        for o in (*old.obligations, *new.obligations):
            c = o.clause
            if c.kind == "before" and (c.first in impacted or c.second in impacted) and c.id not in impacted:
                impacted.add(c.id)
                changed = True
    return tuple(sorted(impacted))


def valid_suffix(snapshot, contract, plan):
    current = snapshot.state
    if any(monitor(contract, current, h)[0] for h in possible_histories(snapshot.history)):
        return False
    for i, event in enumerate(plan):
        matches = [(ns, e) for ns, e in successors(current, snapshot.cursor+i+1)
                   if (e.kind, e.object, e.target, e.seq) == (event.kind, event.object, event.target, event.seq)]
        if len(matches) != 1:
            return False
        current = matches[0][0]
    return all(monitor(contract, current, (*h, *plan)) == (frozenset(), frozenset())
               for h in possible_histories(snapshot.history))


def score(case, contract, decision):
    s, gold = case["snapshot"], case["gold"]
    exact = semantic_key(contract) == semantic_key(gold)
    status_correct = decision.status == case["gold_status"]
    suffix_valid = decision.status == "ready" and valid_suffix(s, gold, decision.plan)
    old = {o.clause.id: o for o in s.contract.obligations}
    wanted = {o.clause.id: o for o in gold.obligations}
    predicted = {o.clause.id: o for o in contract.obligations}
    affected = set(impact_set(s.contract, gold))
    untouched = {k for k, o in old.items() if wanted.get(k) == o and k not in affected}
    retained = sum(predicted.get(k) == old[k] for k in untouched)
    return {"semantic_exact": exact, "status_correct": status_correct,
        "valid_gold_suffix": suffix_valid, "preserved": retained, "preservation_total": len(untouched),
        "unsafe_ready": decision.status == "ready" and not suffix_valid,
        "end_to_end": exact and status_correct and (suffix_valid if case["gold_status"] == "ready" else True),
        "affected_ids": impact_set(s.contract, contract)}


def mechanism_ablation(case):
    s, gold = case["snapshot"], case["gold"]
    # Explicitly discard historical event facts but keep the logical clock;
    # this is a diagnostic ablation, not a sensor-supported alternative state.
    no_history = replace(s, history=tuple(replace(e, evidence="refuted") for e in s.history))
    # Keep only final-state clauses: demonstrates lost trace requirements.
    final_only = Contract(gold.version, tuple(o for o in gold.obligations if o.clause.kind in ("at", "empty")))
    return {"id": case["id"], "family": case["family"], "gold_status": case["gold_status"],
            "full_status": decide(s, gold).status,
            "no_history_status": decide(no_history, gold).status,
            "final_only_status": decide(s, final_only).status}
