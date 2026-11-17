from copy import deepcopy
from dataclasses import asdict

import pytest

from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.fragment_domain_adapter import IndependentActionBound
from ch3.revision.fragment_partial_nogoods import PartialNogoodBank, guard_universe, minimal_guards
from ch3.revision.fragment_runtime import build_fragment_runtime
from ch3.revision.fragments import Atom


def setup(atoms=None, state=None):
    d = build_compact_domain()
    atoms = atoms or (Atom("at", "yellow", "green"), Atom("visit", "blue", "green"), Atom("visit", "red", "table"))
    state = state or d.model.State(yellow="hand", holding="yellow")
    failure = {"source_id": "test", "core": [asdict(a) for a in atoms], "state": asdict(state),
        "key": d.regions.canonical(atoms).key, "horizon": 8, "semantics_sha256": d.fingerprint()}
    fixed = d.regions.RegionBank.verified([], d.old.Oracle())
    return d, atoms, state, failure, fixed


def test_universe_includes_nonvacuous_partial_and_empty_guards():
    d, *_ = setup()
    universe = guard_universe(d)
    assert len(universe) == 125 and universe[()] == (1 << 34)-1
    assert universe[(Atom("loc", "blue", "hand"),)]
    assert universe[(Atom("loc", "blue", "hand"), Atom("loc", "red", "hand"))] == 0


@pytest.mark.parametrize("mode", ["source", "complete"])
def test_every_partial_condition_is_sound_nonvacuous_and_deletion_minimal(mode):
    d, atoms, state, failure, fixed = setup()
    bank = PartialNogoodBank(d, fixed, [failure], mode=mode)
    oracle = d.old.Oracle()
    masks = guard_universe(d)
    has_partial = False
    for record in bank.records:
        for entry in record["bindings"]:
            core = tuple(Atom(**a) for a in entry["atoms"])
            distances = oracle.distances(core)
            bad = sum(1 << i for i, n in enumerate(distances) if n < 0 or n > 8)
            union = 0
            for raw in entry["guards"]:
                guard = tuple(Atom("loc", o, t) for o, t in raw)
                covered = masks[guard]
                assert covered and covered & ~bad == 0
                union |= covered
                has_partial |= len(guard) < 3
                for i in range(len(guard)):
                    assert masks[guard[:i]+guard[i+1:]] & ~bad
            assert union == entry["bad_state_mask"]
            if mode == "complete":
                assert union == bad
    assert has_partial
    assert bank.evidence.__func__.__code__ is d.regions.RegionBank.evidence.__code__


def test_complete_partial_rules_equal_original_full_certificates_at_all_states():
    d, atoms, state, failure, fixed = setup()
    complete = PartialNogoodBank(d, fixed, [failure], mode="complete")
    full = d.regions.RegionBank.verified([d.regions.canonical(atoms)], d.old.Oracle())
    expected = {tuple(sorted(Atom(**a) for a in b["atoms"])): b["bad_state_mask"] for c in full.certificates for b in c["bindings"]}
    for record in complete.records:
        for entry in record["bindings"]:
            assert entry["bad_state_mask"] == expected[tuple(Atom(**a) for a in entry["atoms"])]
    for current in d.states:
        s = d.model.Snapshot(d.old.contract(atoms), current, (), "supported")
        assert complete.match(s) == full.match(s)


def test_source_condition_really_drops_irrelevant_source_positions():
    d, atoms, state, failure, fixed = setup()
    source = PartialNogoodBank(d, fixed, [failure], mode="source")
    s = d.model.Snapshot(d.old.contract(atoms), d.model.State(blue="green", yellow="hand", holding="yellow"), (), "changed")
    assert source.match(s)
    assert d.model.decide(s, s.contract, 8).status == "conflict"


def test_empty_guard_is_retained_for_state_independent_conflict():
    d = build_compact_domain()
    atoms = (Atom("at", "blue", "green"), Atom("at", "yellow", "green"))
    failure = {"source_id": "capacity", "core": [asdict(a) for a in atoms], "state": asdict(d.model.State()),
        "key": d.regions.canonical(atoms).key, "horizon": 8, "semantics_sha256": d.fingerprint()}
    fixed = d.regions.RegionBank.verified([], d.old.Oracle())
    bank = PartialNogoodBank(d, fixed, [failure], mode="complete")
    assert any([] in b["guards"] for r in bank.records for b in r["bindings"])


def test_untrusted_rules_are_recomputed_and_changed_guard_is_rejected():
    d, _, _, failure, fixed = setup()
    bank = PartialNogoodBank(d, fixed, [failure])
    wrong = deepcopy(bank.records)
    entry = next(b for r in wrong for b in r["bindings"] if b["guards"])
    entry["guards"] = []
    with pytest.raises(ValueError, match="untrusted"):
        PartialNogoodBank(d, fixed, [failure], expected_records=wrong)


def test_unknown_history_does_not_create_false_future_visit_requirement():
    d, atoms, state, failure, fixed = setup()
    bank = PartialNogoodBank(d, fixed, [failure], mode="complete")
    m = d.model
    history = (m.Event(1, "place", "blue", "green", "unknown"),)
    s = m.Snapshot(d.old.contract(atoms), state, history, "uncertain").validate()
    assert not bank.match(s)


def test_same_execution_adapter_accepts_partial_rules_without_extra_replanning():
    d, atoms, state, failure, fixed = setup()
    bank = PartialNogoodBank(d, fixed, [failure], mode="source")
    session = d.Session(bank)
    session.freeze()
    bridge = build_fragment_runtime(d, session, IndependentActionBound(d))
    rt = bridge.Runtime(d.model.Snapshot(d.old.contract(atoms), state, (), "supported"))
    assert rt.assess()[0].status == "conflict" and session.backend_calls == 0
    assert bridge.records[-1]["proof"]["kind"] == "partial_state_nogood"


def test_bad_source_budget_and_stale_semantics_are_rejected():
    d, atoms, state, failure, fixed = setup()
    bad = {**failure, "horizon": 7}
    with pytest.raises(ValueError, match="horizon"):
        PartialNogoodBank(d, fixed, [bad])
    bank = PartialNogoodBank(d, fixed, [failure])
    d.model.successors = lambda *_: ()
    with pytest.raises(ValueError, match="changed"):
        bank.match(d.model.Snapshot(d.old.contract(atoms), state, (), "stale"))
