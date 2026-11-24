"""Try a single-history plan before the bounded multi-history search."""

from dataclasses import replace

from ch3.revision import history, runtime
from ch3.revision.evaluation import valid_suffix
from ch3.revision.evidence_resolution import EvidenceResolution
from ch3.revision.fragment_domain_adapter import _class, _function


def candidate_first_planner(*, decide, monitor, possible_histories, validate_suffix):
    def plan(snapshot, contract, horizon):
        uncertain = sum(event.evidence == "unknown" for event in snapshot.history)
        if not uncertain:
            return decide(snapshot, contract, horizon)

        histories = possible_histories(snapshot.history)
        # Prefix violations in the current contract grammar cannot be undone by
        # appending actions. The full solver still classifies the disagreement.
        if any(monitor(contract, snapshot.state, trace)[0] for trace in histories):
            return decide(snapshot, contract, horizon)

        # Resolve the unknowns to absent while preserving the ledger cursor.
        # Use the same bounded planner and never commit the projected history.
        projected = replace(snapshot, history=tuple(
            replace(event, evidence="refuted") if event.evidence == "unknown" else event
            for event in snapshot.history
        ))
        result = decide(projected, contract, horizon)
        if result.status == "ready" and validate_suffix(snapshot, contract, result.plan):
            alternatives = 2**uncertain
            return replace(result, history_alternatives=alternatives, feasible_alternatives=alternatives)
        return decide(snapshot, contract, horizon)

    return plan


def bind_candidate_decision(model, *, validate_suffix, decide=None, rebase_suffix=None):
    """Bind the unchanged suffix-reuse dispatcher to the candidate-first planner."""
    plan = candidate_first_planner(
        decide=model.decide if decide is None else decide,
        monitor=model.monitor,
        possible_histories=model.possible_histories,
        validate_suffix=validate_suffix,
    )
    return _function(runtime.routed_decision, {
        **vars(runtime), "Decision": model.Decision, "decide": plan, "valid_suffix": validate_suffix,
        "rebase_suffix": runtime.rebase_suffix if rebase_suffix is None else rebase_suffix,
    })


_candidate_runtime = _class(runtime.RevisionRuntime, {
    **vars(runtime),
    "routed_decision": bind_candidate_decision(history, validate_suffix=valid_suffix),
})


class CandidateFirstEvidenceRuntime(EvidenceResolution, _candidate_runtime):
    """Retain the original evidence, reauthorization and dispatch semantics."""
