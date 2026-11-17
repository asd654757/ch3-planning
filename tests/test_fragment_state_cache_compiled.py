from dataclasses import asdict
from itertools import combinations

from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.fragment_state_cache import StateBoundBank
from ch3.revision.fragment_state_cache_compiled import CompiledStateBoundBank
from ch3.revision.fragments import Atom


def test_compiled_control_has_identical_coverage_and_same_full_matcher_code():
    d = build_compact_domain()
    source_atoms = (Atom("at", "yellow", "green"), Atom("visit", "blue", "green"), Atom("visit", "red", "table"))
    fixed = d.regions.RegionBank.verified(d.regions.expert_patterns(), d.old.Oracle())
    failure = {"source_id": "test", "core": [asdict(a) for a in source_atoms],
        "state": asdict(d.model.State(yellow="hand", holding="yellow")),
        "key": d.regions.canonical(source_atoms).key, "horizon": 8, "semantics_sha256": d.fingerprint()}
    original = StateBoundBank(d, fixed, [failure])
    compiled = CompiledStateBoundBank(d, fixed, [failure])
    assert compiled.evidence.__func__.__code__ is d.regions.RegionBank.evidence.__code__
    assert compiled.entries == original.entries
    tasks = [source_atoms, (*source_atoms, Atom("empty"))]
    tasks += list(combinations((Atom("at", "blue", "green"), Atom("at", "yellow", "green"),
        Atom("visit", "red", "return"), Atom("forbid", "blue")), 2))
    for atoms in tasks:
        for state in d.states:
            snapshot = d.model.Snapshot(d.old.contract(atoms), state, (), "equivalence").validate()
            assert compiled.match(snapshot) == original.match(snapshot)
            if compiled.match(snapshot):
                assert d.model.decide(snapshot, snapshot.contract, 8).status == "conflict"
