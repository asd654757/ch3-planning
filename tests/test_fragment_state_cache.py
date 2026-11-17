from dataclasses import asdict, replace

import pytest

from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.fragment_domain_adapter import IndependentActionBound
from ch3.revision.fragment_runtime import build_fragment_runtime
from ch3.revision.fragment_state_cache import StateBoundBank
from ch3.revision.fragments import Atom


def fixture():
    d = build_compact_domain()
    atoms = (Atom("at", "yellow", "green"), Atom("visit", "blue", "green"), Atom("visit", "red", "table"))
    state = d.model.State(yellow="hand", holding="yellow")
    failure = {"source_id": "source-test", "core": [asdict(a) for a in atoms], "state": asdict(state),
        "key": d.regions.canonical(atoms).key, "horizon": 8, "semantics_sha256": d.fingerprint()}
    fixed = d.regions.RegionBank.verified([], d.old.Oracle())
    return d, atoms, state, failure, fixed


def test_symmetry_cache_checks_all_permuted_points():
    d, atoms, state, failure, fixed = fixture()
    bank = StateBoundBank(d, fixed, [failure])
    assert len(bank.entries) == 12
    for (index, _), proof in bank.entries.items():
        core = tuple(Atom(**a) for a in proof["support"])
        s = d.model.Snapshot(d.old.contract(core), d.states[index], (), "support").validate()
        assert bank.match(s) and d.model.decide(s, s.contract, 8).status == "conflict"
    assert any(a["target"] == "return" for p in bank.entries.values() for a in p["support"])
    assert all(a["target"] in ("green", "return") for p in bank.entries.values()
               for a in p["support"] if a["kind"] == "at")


def test_state_cache_allows_task_supersets_but_checks_current_state():
    d, atoms, state, failure, fixed = fixture()
    bank = StateBoundBank(d, fixed, [failure])
    s = d.model.Snapshot(d.old.contract((*atoms, Atom("empty"))), state, (), "support").validate()
    assert bank.match(s)
    clear = replace(s, state=d.model.State())
    assert not bank.match(clear)
    assert d.model.decide(clear, clear.contract, 8).status == "ready"


def test_full_certification_can_cover_states_outside_source_orbit():
    d, atoms, state, failure, fixed = fixture()
    cache = StateBoundBank(d, fixed, [failure])
    full = d.regions.RegionBank.verified([d.regions.canonical(atoms)], d.old.Oracle())
    witnesses = []
    for current in d.states:
        s = d.model.Snapshot(d.old.contract(atoms), current, (), "support").validate()
        if full.match(s) and not cache.match(s):
            assert d.model.decide(s, s.contract, 8).status == "conflict"
            witnesses.append(current)
    assert witnesses


def test_past_visit_does_not_become_a_false_future_requirement():
    d, atoms, state, failure, fixed = fixture()
    cache = StateBoundBank(d, fixed, [failure])
    m = d.model
    history = (m.Event(1, "pick", "blue", None), m.Event(2, "place", "blue", "green"),
               m.Event(3, "pick", "blue", None), m.Event(4, "place", "blue", "table"),
               m.Event(5, "pick", "yellow", None))
    s = m.Snapshot(d.old.contract(atoms), state, history, "supported-history").validate()
    assert not cache.match(s)
    assert m.decide(s, s.contract, 8).status == "ready"


def test_modified_feasible_source_state_cannot_be_loaded_as_conflict():
    d, _, _, failure, fixed = fixture()
    failure["state"] = asdict(d.model.State())
    with pytest.raises(ValueError, match="unverified"):
        StateBoundBank(d, fixed, [failure])


@pytest.mark.parametrize("field,value", [("horizon", 7), ("semantics_sha256", "bad"), ("key", "bad")])
def test_modified_domain_horizon_or_pattern_is_rejected(field, value):
    d, _, _, failure, fixed = fixture()
    failure[field] = value
    with pytest.raises(ValueError):
        StateBoundBank(d, fixed, [failure])


def test_state_cache_uses_same_guarded_execution_without_full_backend():
    d, atoms, state, failure, fixed = fixture()
    bank = StateBoundBank(d, fixed, [failure])
    session = d.Session(bank)
    session.freeze()
    bridge = build_fragment_runtime(d, session, IndependentActionBound(d))
    rt = bridge.Runtime(d.model.Snapshot(d.old.contract(atoms), state, (), "support"))
    assert rt.assess()[0].status == "conflict" and session.backend_calls == 0
    assert bridge.records[-1]["proof"]["kind"] == "state_bound_nogood"
    rt.observe(d.model.State(), observation_id="new-state")
    assert rt.assess()[0].status == "ready" and session.backend_calls == 1
    assert bridge.unchanged_runtime_methods


def test_stale_executing_semantics_invalidates_state_cache():
    d, atoms, state, failure, fixed = fixture()
    bank = StateBoundBank(d, fixed, [failure])
    d.model.successors = lambda *_: ()
    with pytest.raises(ValueError, match="changed"):
        bank.match(d.model.Snapshot(d.old.contract(atoms), state, (), "support"))
