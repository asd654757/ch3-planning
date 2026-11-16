from itertools import combinations
import random

import pytest

from ch3.revision import history, fragment_regions
from ch3.revision.fragments import Atom
from ch3.revision.fragment_domain_adapter import build_domain, IndependentActionBound


def snapshot(domain, atoms, state=None):
    return domain.model.Snapshot(domain.old.contract(atoms), state or domain.model.State(), (), "adapter-test").validate()


def test_registered_states_and_single_hand_capacity():
    d = build_domain()
    assert len(d.states) == 34
    assert len({d.state_key(s) for s in d.states}) == 34
    with pytest.raises(ValueError):
        d.model.State(blue="green", red="green").validate()
    with pytest.raises(ValueError):
        d.model.State(red="hand").validate()
    assert d.model.State(red="hand", holding="red").validate()


def test_adapter_does_not_mutate_original_and_keeps_algorithm_code():
    original = history.OBJECTS
    d = build_domain()
    assert history.OBJECTS is original and len(fragment_regions.STATES) == 13
    assert all(d.unchanged_algorithms.values())
    assert d.fingerprint() != build_domain(("blue", "yellow")).fingerprint()


def test_third_object_is_checked_and_original_search_produces_plan():
    d = build_domain()
    s = snapshot(d, [Atom("at", "red", "green")])
    result = d.model.decide(s, s.contract, 8)
    assert result.status == "ready" and len(result.plan) == 2
    assert all(e.object == "red" for e in result.plan)
    with pytest.raises(ValueError):
        history.Clause(id="bad", kind="at", object="red", target="green")


def test_feature_index_distinguishes_third_object():
    d = build_domain()
    a = snapshot(d, [Atom("visit", "blue", "green")])
    b = snapshot(d, [Atom("visit", "blue", "green")], d.model.State(red="green"))
    assert d.features(a)[0] == d.features(b)[0]
    assert d.features(a)[1] != d.features(b)[1]


def test_frozen_session_does_not_fill_target_cache():
    d = build_domain()
    bank = d.regions.RegionBank.verified([], d.old.Oracle())
    session = d.Session(bank, learn=True)
    session.freeze()
    s = snapshot(d, [Atom("at", "red", "green")])
    assert session.resolve(s).status == "ready"
    assert session.resolve(s).origin == "backend"
    assert not session.memo and not session.patterns


def test_three_object_certificate_cannot_be_loaded_in_two_object_domain():
    d = build_domain()
    pattern = d.regions.canonical([Atom("visit", "red", "green"), Atom("forbid", "red")])
    bank = d.regions.RegionBank.verified([pattern], d.old.Oracle())
    two = build_domain(("blue", "yellow"))
    with pytest.raises(ValueError):
        two.regions.RegionBank(bank.certificates)


def test_independent_bound_never_exceeds_found_plan_length():
    d = build_domain()
    bound = IndependentActionBound(d)
    rng = random.Random(60216)
    pool = [Atom(k, o, t) for k in ("at", "visit") for o in d.model.OBJECTS for t in d.model.TARGETS]
    pool += [Atom("forbid", o) for o in d.model.OBJECTS] + [Atom("empty")]
    ready = rejected = 0
    for _ in range(160):
        atoms = rng.sample(pool, rng.randrange(1, 6))
        s = snapshot(d, atoms, rng.choice(d.states))
        result = d.model.decide(s, s.contract, 8)
        lower = bound.value(s)
        if result.status == "ready":
            ready += 1
            assert lower <= len(result.plan)
        if lower > 8:
            rejected += 1
            assert result.status == "conflict"
    assert ready and rejected


def test_two_object_features_and_solver_match_small_original_grid():
    d = build_domain(("blue", "yellow"))
    atoms = [Atom("at", "blue", "green"), Atom("visit", "yellow", "return"), Atom("forbid", "blue")]
    for pair in combinations(atoms, 2):
        for state in d.states:
            s = snapshot(d, pair, state)
            original_state = history.State(**{o: getattr(state, o) for o in history.OBJECTS}, holding=state.holding)
            from ch3.revision.fragments import contract
            original = history.Snapshot(contract(pair), original_state, (), "original")
            assert d.features(s) == fragment_regions.features(original)
            assert d.model.decide(s, s.contract, 8).status == history.decide(original, original.contract, 8).status


def test_isolated_clause_and_snapshot_json_roundtrip(tmp_path):
    from scripts.benchmark_fragment_three_objects import write, load
    d = build_domain()
    s = snapshot(d, [Atom("at", "red", "green"), Atom("visit", "blue", "return")])
    path = tmp_path / "cases.json"
    write(path, [{"id": "roundtrip", "snapshot": s}])
    restored = load(d, path)[0]["snapshot"]
    assert d.features(restored) == d.features(s)
    assert restored.digest == s.digest
