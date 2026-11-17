from dataclasses import replace

import pytest

from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.fragment_domain_adapter import IndependentActionBound
from ch3.revision.fragment_purpose_repair import (
    OptionFeatures, PurposePolicy, SharedReadyCache, admissible, candidates, propose, variables)
from ch3.revision.fragments import Atom


def snapshot(d, clauses, *, state=None, history=(), since=0):
    m = d.model
    return m.Snapshot(m.Contract(3, tuple(m.Obligation(m.Clause(**c), since) for c in clauses)),
                      state or m.State(), history, "supported").validate()


def policy(d, s, objects=None):
    return PurposePolicy("test-explicit-buffer-permission", s.digest, objects or d.model.OBJECTS)


def repair_fixture():
    d = build_compact_domain()
    s = snapshot(d, [{"id": "goal", "kind": "at", "object": "blue", "target": "green"},
        {"id": "safe", "kind": "forbid", "object": "yellow"},
        {"id": "visit", "kind": "visit", "object": "red", "target": "table"}],
        state=d.model.State(yellow="green"))
    atoms = (Atom("at", "blue", "green"), Atom("forbid", "yellow"))
    bank = d.regions.RegionBank.verified([d.regions.canonical(atoms)], d.old.Oracle())
    return d, s, bank


def test_group_swap_preserves_both_coreference_and_distinct_visit_targets():
    d = build_compact_domain()
    s = snapshot(d, [{"id": "a", "kind": "at", "object": "blue", "target": "green"},
        {"id": "v1", "kind": "visit", "object": "blue", "target": "green"},
        {"id": "v2", "kind": "visit", "object": "blue", "target": "return"},
        {"id": "v3", "kind": "visit", "object": "blue", "target": "table"}])
    rows = candidates(d, s, policy(d, s))
    assert len(rows) == 2 and rows[1].cost == 3
    assert [o.clause.target for o in rows[1].contract.obligations] == ["return", "return", "green", "table"]
    assert all(admissible(d, s, policy(d, s), r.contract) for r in rows)
    assert len(rows[1].contract.obligations) == len(s.contract.obligations)


@pytest.mark.parametrize("evidence,locked", [("confirmed", True), ("unknown", True), ("refuted", False)])
def test_history_supported_or_disputed_visit_locks_entire_object(evidence, locked):
    d = build_compact_domain()
    event = d.model.Event(1, "place", "blue", "green", evidence)
    s = snapshot(d, [{"id": "v", "kind": "visit", "object": "blue", "target": "green"},
        {"id": "g", "kind": "at", "object": "blue", "target": "return"}], history=(event,))
    assert ("blue" not in variables(d, s, policy(d, s))) == locked


def test_stale_policy_and_unregistered_or_table_equivalence_are_rejected():
    d, s, _ = repair_fixture()
    with pytest.raises(ValueError, match="stale"):
        candidates(d, replace(s, observation_id="new"), policy(d, s))
    with pytest.raises(ValueError, match="buffers"):
        candidates(d, s, replace(policy(d, s), equivalent_targets=("green", "table")))
    with pytest.raises(ValueError, match="object permission"):
        candidates(d, s, replace(policy(d, s), swappable_objects=("unknown",)))


def test_compiler_catches_a_conflict_introduced_only_by_replacement():
    d = build_compact_domain()
    s = snapshot(d, [{"id": "a", "kind": "at", "object": "blue", "target": "green"},
        {"id": "b", "kind": "at", "object": "yellow", "target": "return"}])
    atoms = (Atom("at", "blue", "green"), Atom("at", "yellow", "green"))
    bank = d.regions.RegionBank.verified([d.regions.canonical(atoms)], d.old.Oracle())
    p = policy(d, s)
    compiled = OptionFeatures(d, bank, s, p)
    assert bank.evidence(s) is None and not compiled.blocked(())
    assert compiled.blocked(("blue",))
    for state in d.states:
        current = replace(s, state=state)
        p = policy(d, current)
        compiled = OptionFeatures(d, bank, current, p)
        for row in candidates(d, current, p):
            mask, _ = d.features(replace(current, contract=row.contract))
            assert mask == compiled.feature_mask(row.swapped_objects)
            assert compiled.blocked(row.swapped_objects) == (
                bank.evidence(replace(current, contract=row.contract)) is not None)


@pytest.mark.parametrize("method", ["standard", "recheck", "compiled"])
def test_actual_substitution_repairs_blocked_goal_without_deleting_requirements(method):
    d, s, bank = repair_fixture()
    assert d.model.decide(s, s.contract, 8).status == "conflict"
    original = s.digest
    result = propose(d, s, policy(d, s), bank, IndependentActionBound(d), SharedReadyCache(d),
                     method=method, known_nonready=True)
    assert result.status == "proposal" and result.cost == 1 and result.swapped_objects == ("blue",)
    assert result.edits[0].previous_target == "green" and result.edits[0].target == "return"
    assert admissible(d, s, policy(d, s), result.contract)
    assert d.model.decide(s, result.contract, 8).status == "ready"
    assert s.digest == original and len(result.contract.obligations) == len(s.contract.obligations)


def test_retained_historical_violation_cannot_be_repaired_by_reanchoring():
    d = build_compact_domain()
    event = d.model.Event(1, "pick", "blue", None)
    s = snapshot(d, [{"id": "g", "kind": "at", "object": "blue", "target": "green"},
        {"id": "locked", "kind": "forbid", "object": "blue"}], history=(event,))
    bank = d.regions.RegionBank.verified([], d.old.Oracle())
    result = propose(d, s, policy(d, s), bank, IndependentActionBound(d), SharedReadyCache(d),
                     method="recheck", known_nonready=True)
    assert result.status == "no_permitted_substitution"
    assert s.contract.obligations[1].since == 0 and s.history == (event,)


def test_independent_checker_rejects_partial_group_swap_and_deletion():
    d = build_compact_domain()
    s = snapshot(d, [{"id": "a", "kind": "at", "object": "blue", "target": "green"},
        {"id": "v", "kind": "visit", "object": "blue", "target": "green"}])
    row = candidates(d, s, policy(d, s))[1]
    partial = replace(row.contract, obligations=(row.contract.obligations[0], s.contract.obligations[1]))
    deleted = replace(row.contract, obligations=row.contract.obligations[:1])
    assert not admissible(d, s, policy(d, s), partial)
    assert not admissible(d, s, policy(d, s), deleted)


def test_replacement_anchor_rule_equals_existing_api_and_preserves_before():
    d = build_compact_domain()
    m = d.model
    s = snapshot(d, [{"id": "v1", "kind": "visit", "object": "blue", "target": "green"},
        {"id": "v2", "kind": "visit", "object": "yellow", "target": "return"},
        {"id": "order", "kind": "before", "first": "v1", "second": "v2"}],
        history=(m.Event(4, "pick", "red", None),))
    for row in candidates(d, s, policy(d, s)):
        replacement = m.Replacement(version=s.contract.version, snapshot=s.digest,
                                    clauses=[o.clause for o in row.contract.obligations])
        assert m.apply_replacement(s, replacement) == row.contract
        assert row.contract.obligations[2] == s.contract.obligations[2]


@pytest.mark.parametrize("method", ["standard", "recheck", "compiled"])
def test_corrupt_ready_cache_never_grants_execution_and_solver_falls_back(method):
    d, s, bank = repair_fixture()
    p = policy(d, s)
    row = candidates(d, s, p)[1]
    current = replace(s, contract=row.contract)
    key = d.Session.resolve.__globals__["exact_key"](current)
    bad = [{"key": key, "status": "ready", "plan": [{"seq": 1, "kind": "pick", "object": "yellow",
        "target": None, "evidence": "confirmed", "observation_id": "corrupt"}]}]
    result = propose(d, s, p, bank, IndependentActionBound(d), SharedReadyCache(d, bad),
                     method=method, known_nonready=True)
    assert result.status == "proposal" and result.stats["invalid_ready_cache"] == 1
    assert result.stats["backend_calls"] == 1


def test_foreign_components_are_rejected_even_in_standard_mode():
    d, s, bank = repair_fixture()
    other = build_compact_domain()
    with pytest.raises(ValueError, match="foreign"):
        propose(d, s, policy(d, s), bank, IndependentActionBound(other), SharedReadyCache(d), method="standard")
