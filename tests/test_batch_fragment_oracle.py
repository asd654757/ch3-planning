from dataclasses import asdict
import json
import random

import pytest

from ch3.revision.fragments import Atom
from ch3.revision.fragment_domain_adapter import build_domain
from ch3.revision.batch_fragment_oracle import BatchOracle, build_batch_domain


def test_distinct_initial_states_share_one_profile_and_keep_shortest_suffix():
    d = build_domain()
    oracle = BatchOracle(d)
    atoms = (Atom("at", "blue", "green"), Atom("visit", "yellow", "return"))
    for state in d.states:
        expected = d.old.Oracle().solve(atoms, state, 8)
        assert oracle.solve(atoms, state, 8) == expected
    assert oracle.calls == 34 and len(oracle.profiles) == 1


def test_object_and_unit_region_symmetries_share_search_but_keep_raw_tie_break():
    d = build_domain()
    oracle = BatchOracle(d)
    for atoms in ((Atom("visit", "red", "green"), Atom("visit", "yellow", "return")),
                  (Atom("visit", "blue", "return"), Atom("visit", "red", "green"))):
        for state in d.states:
            assert oracle.solve(atoms, state, 8) == d.old.Oracle().solve(atoms, state, 8)
    assert len(oracle.profiles) == 1 and len(oracle.views) == 2


def test_table_is_not_interchanged_with_unit_capacity_area():
    d = build_domain()
    oracle = BatchOracle(d)
    state = d.model.State()
    unit = [Atom("at", "blue", "green"), Atom("at", "yellow", "green")]
    unlimited = [Atom("at", "blue", "table"), Atom("at", "yellow", "table")]
    assert oracle.solve(unit, state, 8).status == "conflict"
    assert oracle.solve(unlimited, state, 8).status == "ready"
    assert len(oracle.profiles) == 2


@pytest.mark.parametrize("horizon", [0, 1, 4, 8, 12])
def test_horizons_protected_objects_and_pending_visit_semantics(horizon):
    d = build_domain()
    oracle = BatchOracle(d)
    tasks = [[], [Atom("empty")], [Atom("visit", "blue", "green")],
             [Atom("visit", "blue", "green"), Atom("forbid", "blue")],
             [Atom("at", "blue", "green"), Atom("visit", "blue", "return"),
              Atom("visit", "yellow", "green")]]
    for atoms in tasks:
        for state in d.states[::5]:
            assert oracle.solve(atoms, state, horizon) == d.old.Oracle().solve(atoms, state, horizon)


def test_deterministic_random_cores_match_original_full_decisions():
    d = build_domain()
    oracle = BatchOracle(d)
    rng = random.Random(11691)
    pool = [Atom(k, o, t) for k in ("at", "visit") for o in d.model.OBJECTS for t in d.model.TARGETS]
    pool += [Atom("forbid", o) for o in d.model.OBJECTS] + [Atom("empty")]
    for _ in range(50):
        atoms = rng.sample(pool, rng.randint(1, 5))
        state = rng.choice(d.states)
        horizon = rng.choice((0, 4, 8, 12))
        assert oracle.solve(atoms, state, horizon) == d.old.Oracle().solve(atoms, state, horizon)


def test_changed_runtime_semantics_invalidates_cached_distances():
    d = build_domain()
    oracle = BatchOracle(d)
    oracle.solve([], d.model.State(), 8)
    d.model.TARGETS = (*d.model.TARGETS, "new")
    with pytest.raises(ValueError, match="rebuild"):
        oracle.solve([], d.model.State(), 8)


def test_batch_domain_preserves_core_compiler_and_fallback_and_recertifies_json():
    d = build_batch_domain()
    assert all(d.unchanged_algorithms.values())
    pattern = d.regions.canonical([Atom("visit", "blue", "green"), Atom("forbid", "blue")])
    bank = d.regions.RegionBank.verified([pattern], d.old.Oracle())
    raw = json.loads(json.dumps(bank.certificates))
    restored = d.regions.RegionBank(raw)
    assert restored.conditions == bank.conditions
    raw[0]["bindings"][0]["bad_state_mask"] ^= 1
    with pytest.raises(ValueError, match="untrusted"):
        d.regions.RegionBank(raw)


def test_invalid_horizon_and_object_are_not_hidden_by_cache():
    d = build_domain()
    oracle = BatchOracle(d)
    with pytest.raises(ValueError):
        oracle.solve([], d.model.State(), True)
    with pytest.raises(ValueError):
        oracle.solve([Atom("at", "unknown", "green")], d.model.State(), 8)
