from dataclasses import replace

import pytest

from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.fragment_domain_adapter import IndependentActionBound
from ch3.revision.fragment_repair_frozen import METHODS, propose_frozen
from ch3.revision.fragments import Atom


@pytest.mark.parametrize("method", METHODS)
def test_frozen_repair_preserves_hard_goal_and_cannot_reset_history(method):
    d = build_compact_domain()
    m = d.model
    atoms = (Atom("at", "blue", "green"), Atom("forbid", "blue"))
    bank = d.regions.RegionBank.verified([d.regions.canonical(atoms)], d.old.Oracle())
    task = m.Contract(1, (m.Obligation(m.Clause(id="goal", kind="at", object="blue", target="green")),
        m.Obligation(m.Clause(id="locked", kind="forbid", object="blue")),
        m.Obligation(m.Clause(id="visit", kind="visit", object="blue", target="green"))))
    s = m.Snapshot(task, m.State(), (), "supported")
    result = propose_frozen(d, s, bank, IndependentActionBound(d), method, known_nonready=True)
    assert result.status == "no_authorized_repair"
    # Removing the independently hard prohibition lets the retained hard goal run.
    s = replace(s, contract=m.Contract(1, (task.obligations[0], task.obligations[2])))
    result = propose_frozen(d, s, bank, IndependentActionBound(d), method)
    assert result.cost == 0 and not result.deleted
    assert result.contract.obligations == s.contract.obligations
