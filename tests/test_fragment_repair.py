from dataclasses import replace

import pytest

from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.fragment_domain_adapter import IndependentActionBound
from ch3.revision.fragment_repair import METHODS, SupportConstraints, propose, retained_contract
from ch3.revision.fragments import Atom


def setup():
    d = build_compact_domain()
    m = d.model
    atoms = (Atom("at", "blue", "green"), Atom("forbid", "blue"))
    bank = d.regions.RegionBank.verified([d.regions.canonical(atoms)], d.old.Oracle())
    clauses = (m.Clause(id="goal", kind="at", object="blue", target="green"),
               m.Clause(id="waypoint", kind="visit", object="blue", target="green"),
               m.Clause(id="locked", kind="forbid", object="blue"))
    s = m.Snapshot(m.Contract(1, tuple(m.Obligation(c) for c in clauses)), m.State(), (), "test")
    return d, bank, s


def test_all_supports_must_be_removed_for_derived_visit():
    d, _, s = setup()
    atoms = (Atom("visit", "blue", "green"), Atom("forbid", "blue"))
    bank = d.regions.RegionBank.verified([d.regions.canonical(atoms)], d.old.Oracle())
    p = SupportConstraints(d, bank, s)
    assert p.blocked(("goal",)) and p.blocked(("waypoint",))
    assert not p.blocked(("goal", "waypoint"))
    assert not p.blocked(("goal",), union=True)
    for _, ids, task in __import__("ch3.revision.fragment_repair", fromlist=["candidates"]).candidates(
            d, s, ("goal", "waypoint", "locked"), {i: 1 for i in ("goal", "waypoint", "locked")}):
        assert p.blocked(ids) == (bank.evidence(replace(s, contract=task)) is not None)


@pytest.mark.parametrize("method", METHODS)
def test_exact_minimum_repair_respects_locked_and_original_snapshot(method):
    d, bank, s = setup()
    before = s.digest
    result = propose(d, s, bank, IndependentActionBound(d), method,
                     editable=("goal", "waypoint"), known_nonready=True)
    assert result.status == "proposal" and result.cost == 2
    assert result.deleted == ("goal", "waypoint")
    assert result.contract.obligations == (s.contract.obligations[2],)
    assert s.digest == before
    assert d.model.decide(s, result.contract, 8).status == "ready"


def test_hard_unsatisfiable_contract_has_no_authorized_repair():
    d, bank, s = setup()
    result = propose(d, s, bank, IndependentActionBound(d), "transfer_support", editable=())
    assert result.status == "no_authorized_repair"


def test_historical_violation_is_not_erased_or_reanchored():
    d, bank, s = setup()
    m = d.model
    event = m.Event(1, "pick", "blue", None)
    s = replace(s, state=m.State(blue="hand", holding="blue"), history=(event,))
    result = propose(d, s, bank, IndependentActionBound(d), "transfer_support",
                     editable=("goal", "waypoint"))
    assert result.status == "no_authorized_repair"
    task = retained_contract(d, s, ("goal", "waypoint"))
    assert task.obligations[0].since == 0 and s.history == (event,)


def test_order_reference_cannot_be_left_dangling():
    d, bank, s = setup()
    m = d.model
    a = m.Clause(id="a", kind="visit", object="blue", target="green")
    b = m.Clause(id="b", kind="visit", object="yellow", target="green")
    order = m.Clause(id="order", kind="before", first="a", second="b")
    s = replace(s, contract=m.Contract(1, tuple(m.Obligation(c) for c in (a, b, order))))
    with pytest.raises(ValueError, match="order must reference"):
        retained_contract(d, s, ("a",))


def test_weighted_minimum_and_uncertain_history_use_original_solver():
    d, bank, s = setup()
    result = propose(d, s, bank, IndependentActionBound(d), "transfer_support",
                     editable=("goal", "waypoint", "locked"),
                     costs={"goal": 1, "waypoint": 1, "locked": 3})
    assert result.deleted == ("goal", "waypoint") and result.cost == 2
    s = replace(s, history=(d.model.Event(1, "place", "blue", "green", "unknown"),))
    result = propose(d, s, bank, IndependentActionBound(d), "transfer_support", editable=())
    assert result.status == "no_authorized_repair"


def test_reject_invalid_edit_costs_and_unknown_permissions():
    d, bank, s = setup()
    with pytest.raises(ValueError, match="positive"):
        propose(d, s, bank, IndependentActionBound(d), "enumerate", editable=("goal",), costs={"goal": 0})
    with pytest.raises(ValueError, match="invalid editable"):
        propose(d, s, bank, IndependentActionBound(d), "enumerate", editable=("absent",))
