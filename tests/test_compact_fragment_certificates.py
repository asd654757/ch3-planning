import json

import pytest

from ch3.revision.fragments import Atom
from ch3.revision.fragment_domain_adapter import build_domain
from ch3.revision.compact_fragment_certificates import build_compact_domain


def test_compact_certificate_keeps_every_binding_and_state_with_exact_distances():
    d = build_compact_domain()
    original = build_domain()
    pattern = d.regions.canonical([Atom("at", "blue", "green"), Atom("visit", "yellow", "green")])
    cert = d.regions.certify_region(pattern, d.old.Oracle())
    for binding in cert["bindings"]:
        atoms = [Atom(**a) for a in binding["atoms"]]
        assert len(binding["minimum_actions"]) == 34 and "states" not in binding
        for i, state in enumerate(original.states):
            expected = original.old.Oracle().solve(atoms, state, 8)
            length = binding["minimum_actions"][i]
            assert bool(binding["bad_state_mask"] & (1 << i)) == (expected.status == "conflict")
            if expected.status == "ready":
                assert length == len(expected.plan)


def test_tampered_distance_is_recomputed_even_if_conflict_mask_unchanged():
    d = build_compact_domain()
    pattern = d.regions.canonical([Atom("visit", "blue", "green"), Atom("forbid", "blue")])
    bank = d.regions.RegionBank.verified([pattern], d.old.Oracle())
    raw = json.loads(json.dumps(bank.certificates))
    assert d.regions.RegionBank(raw).conditions == bank.conditions
    raw[0]["bindings"][0]["minimum_actions"][0] = 99
    with pytest.raises(ValueError, match="untrusted"):
        d.regions.RegionBank(raw)


@pytest.mark.parametrize("horizon", [0, 4, 8, 12])
def test_distances_preserve_budget_boundary(horizon):
    d = build_compact_domain()
    original = build_domain()
    atoms = [Atom("at", "blue", "green"), Atom("visit", "blue", "return"),
             Atom("visit", "yellow", "green")]
    oracle = d.old.Oracle()
    distances = oracle.distances(atoms)
    for i, state in enumerate(original.states):
        expected = original.old.Oracle().solve(atoms, state, horizon)
        assert (distances[i] < 0 or distances[i] > horizon) == (expected.status == "conflict")


def test_history_and_full_task_backend_stay_original():
    d = build_compact_domain()
    assert not d.unchanged_algorithms["regions.certify_region"]
    assert all(v for k, v in d.unchanged_algorithms.items() if k != "regions.certify_region")
    assert d.Session.resolve.__globals__["decide"] is d.model.decide
