"""Ablation: reuse frozen conflict knowledge without target-time core shrinking.

This is the simpler alternative prompted by the first pilot's negative result.
Candidate-wise certificate matching has exactly the same information/coverage
as the support formula and is retained as a strong baseline.
"""
from dataclasses import replace

from ch3.revision.fragment_repair import RepairProposal, SupportConstraints, candidates, editable_ids


METHODS = ("enumerate", "transfer_union", "transfer_support", "transfer_recheck")


def propose_frozen(domain, snapshot, bank, lower, method, *, horizon=8, known_nonready=False):
    snapshot.validate()
    if method not in METHODS or horizon != bank.horizon:
        raise ValueError("unknown method or mismatched horizon")
    editable = editable_ids(snapshot)
    rows = candidates(domain, snapshot, editable, {i: 1 for i in editable})
    constraints = SupportConstraints(domain, bank, snapshot) if method in (
        "transfer_union", "transfer_support") else None
    stats = {k: 0 for k in ("backend_calls", "lower_bound_rejections", "certificate_rejections",
        "known_nonready_reuses", "candidates_considered")}
    stats["candidate_space"] = len(rows)
    stats["transferred_conditions"] = len(constraints.groups) if constraints else 0
    for cost, ids, task in rows:
        stats["candidates_considered"] += 1
        if not ids and known_nonready:
            stats["known_nonready_reuses"] += 1
            continue
        if constraints is not None and constraints.blocked(ids, union=method == "transfer_union"):
            stats["certificate_rejections"] += 1
            continue
        current = replace(snapshot, contract=task)
        if method == "transfer_recheck" and bank.evidence(current) is not None:
            stats["certificate_rejections"] += 1
            continue
        if lower.match(current, horizon):
            stats["lower_bound_rejections"] += 1
            continue
        stats["backend_calls"] += 1
        result = domain.model.decide(current, task, horizon)
        if result.status == "ready":
            return RepairProposal("proposal", ids, cost, task, result.plan, dict(stats))
    return RepairProposal("no_authorized_repair", (), None, None, (), dict(stats))
