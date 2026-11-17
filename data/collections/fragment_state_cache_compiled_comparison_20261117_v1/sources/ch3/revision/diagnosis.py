"""Executable conflict certificates and targeted historical-evidence questions.

These are bounded model diagnostics, not automatic changes to user intent.
All proposed relaxations require a subsequent explicit task revision.
"""
from dataclasses import dataclass, replace
from itertools import combinations, product

from ch3.revision.history import (
    Contract, Obligation, Snapshot, decide, monitor, possible_histories,
)


def dependent_ids(contract, ids):
    removed = set(ids)
    for o in contract.obligations:
        c = o.clause
        if c.kind == "before" and (c.first in removed or c.second in removed):
            removed.add(c.id)
    return removed


def remove_clauses(contract, ids):
    removed = dependent_ids(contract, ids)
    return Contract(contract.version, tuple(o for o in contract.obligations if o.clause.id not in removed)).validate()


def conflict_core(snapshot, contract, horizon=8):
    """Deletion-minimal under dependency-closed clause removal, not minimum size."""
    if decide(snapshot, contract, horizon).status != "conflict":
        return ()
    core = contract
    for id in sorted(o.clause.id for o in contract.obligations):
        candidate = remove_clauses(core, {id})
        if decide(snapshot, candidate, horizon).status == "conflict":
            core = candidate
    return tuple(sorted(o.clause.id for o in core.obligations))


def minimal_evidence_query(snapshot, contract, horizon=8):
    """Smallest event set whose evidence resolution removes all uncertain decisions.

    This asks for archived evidence about OLD events. A fresh current image
    cannot generally settle whether a past visit happened. Query availability
    is outside this bounded model; no new observation is fabricated here.
    """
    if decide(snapshot, contract, horizon).status != "need_observation":
        return ()
    unknown = [e.seq for e in snapshot.history if e.evidence == "unknown"]
    for size in range(1, len(unknown)+1):
        for ids in combinations(unknown, size):
            resolved = True
            for bits in product((False, True), repeat=size):
                values = dict(zip(ids, bits))
                history = tuple(replace(e, evidence="confirmed" if values[e.seq] else "refuted")
                                if e.seq in values else e for e in snapshot.history)
                branch = replace(snapshot, history=history)
                if decide(branch, contract, horizon).status == "need_observation":
                    resolved = False
                    break
            if resolved:
                return ids
    return tuple(unknown)


def reset_visits(snapshot, contract, ids):
    """A proposed scope change only: old physical events remain byte-identical."""
    known = {o.clause.id: o for o in contract.obligations}
    if any(k not in known or known[k].clause.kind != "visit" for k in ids):
        raise ValueError("only visit clauses can be restarted")
    names = {k: f"{k}__restart_v{contract.version+1}" for k in ids}
    if set(names.values()) & known.keys():
        raise ValueError("restart id collision")
    revised = []
    for o in contract.obligations:
        c = o.clause
        if c.id in names:
            revised.append(Obligation(c.model_copy(update={"id": names[c.id]}), snapshot.cursor))
        elif c.kind == "before" and (c.first in names or c.second in names):
            revised.append(Obligation(c.model_copy(update={
                "first": names.get(c.first, c.first), "second": names.get(c.second, c.second)}), snapshot.cursor))
        else:
            revised.append(o)
    return Contract(contract.version+1, tuple(revised)).validate()


@dataclass(frozen=True)
class RepairOption:
    kind: str
    changed_ids: tuple[str, ...]
    proposed: Contract
    original_task_satisfied: bool = False
    requires_explicit_revision: bool = True


def repair_options(snapshot, contract, horizon=8):
    """Feasible one-clause relaxations and smallest-size visit restart subsets.

    Not an exhaustive repair frontier: no invented goals, motions, or resources.
    Suggestions do not count as completing the original conflicting request.
    """
    if decide(snapshot, contract, horizon).status != "conflict":
        return ()
    options = []
    for o in contract.obligations:
        candidate = remove_clauses(contract, {o.clause.id})
        if decide(snapshot, candidate, horizon).status == "ready":
            options.append(RepairOption("relax_requirement", tuple(sorted(dependent_ids(contract, {o.clause.id}))), candidate))
    visits = [o.clause.id for o in contract.obligations if o.clause.kind == "visit"]
    for size in range(1, len(visits)+1):
        found = []
        for ids in combinations(visits, size):
            candidate = reset_visits(snapshot, contract, ids)
            if decide(snapshot, candidate, horizon).status == "ready":
                found.append(RepairOption("prospective_restart", tuple(ids), candidate))
        if found:
            options.extend(found)
            break
    return tuple(options)


def diagnose(snapshot, contract, horizon=8):
    decision = decide(snapshot, contract, horizon)
    histories = tuple(possible_histories(snapshot.history))
    irreversible = bool(histories) and all(monitor(contract, snapshot.state, h)[0] for h in histories)
    return {"status": decision.status, "snapshot": snapshot.digest, "horizon": horizon,
            "conflict_core": conflict_core(snapshot, contract, horizon),
            "core_definition": "deletion_minimal_under_dependency_closed_removal",
            "historical_violation_in_all_evidence_worlds": irreversible,
            "query_old_event_ids": minimal_evidence_query(snapshot, contract, horizon),
            "query_source": "archived_event_evidence_or_user_scope_clarification",
            "repair_options": repair_options(snapshot, contract, horizon)}
