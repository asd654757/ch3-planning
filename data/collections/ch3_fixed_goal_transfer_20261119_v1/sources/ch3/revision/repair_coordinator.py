"""Connect issued purpose-repair proposals to the existing guarded runtime.

The caller supplies explicit task permission and chooses whether to adopt a
proposal. Commands and observations still use the original runtime unchanged.
No physical action, user intent inference, or new repair algorithm lives here.
"""
from dataclasses import dataclass

from ch3.revision.fragment_purpose_repair import admissible, propose


@dataclass(frozen=True)
class IssuedRepair:
    generation: int
    policy: object
    proposal: object


class RepairCoordinator:
    def __init__(self, domain, runtime, session, lower, cache, *, method="recheck"):
        self.domain, self.runtime, self.session = domain, runtime, session
        self.lower, self.cache, self.method = lower, cache, method
        self._issued = None
        self.applied = []
        self._ensure()

    def _ensure(self):
        d, rt, session = self.domain, self.runtime, self.session
        if (session.learning or session.cache_writable or self.lower.domain is not d or self.cache.domain is not d
                or session.bank._token != d.regions.runtime_token() or rt.horizon != session.bank.horizon
                or type(rt.snapshot.state) is not d.model.State):
            raise ValueError("repair coordinator requires the same frozen executing domain")

    def suggest(self, policy):
        self._ensure()
        rt = self.runtime
        if rt.decision is None or rt.decision.status != "conflict" or not rt.pending:
            raise ValueError("repair requires a current conflict; observation uncertainty must remain pending")
        self._issued = None
        result = propose(self.domain, rt.snapshot, policy, self.session.bank, self.lower, self.cache,
                         method=self.method, horizon=rt.horizon, known_nonready=True)
        issued = IssuedRepair(rt.generation, policy, result)
        if result.status == "proposal":
            self._issued = issued
        return issued

    def apply(self, issued):
        """Adopt only this coordinator's live issued result via Runtime.accept."""
        self._ensure()
        rt, d = self.runtime, self.domain
        if (issued is not self._issued or issued.generation != rt.generation
                or issued.proposal.snapshot_digest != rt.snapshot.digest):
            raise ValueError("stale, unissued, or already adopted repair")
        result, policy = issued.proposal, issued.policy
        if (result.status != "proposal" or result.policy_id != policy.id
                or not admissible(d, rt.snapshot, policy, result.contract)
                or result.cost != len(result.edits) or result.cost <= 0):
            raise ValueError("repair is outside its explicit purpose policy")
        before, generation = rt.snapshot, rt.generation
        alternatives = 2**sum(e.evidence == "unknown" for e in before.history)
        ready = d.model.Decision("ready", result.plan, alternatives, alternatives, rt.horizon)
        decision, route = rt.accept(result.contract, expected_digest=result.snapshot_digest,
            expected_generation=issued.generation, supplied_decision=ready)
        assert rt.snapshot.state == before.state and rt.snapshot.history == before.history
        self._issued = None
        self.applied.append({"before": before, "after": rt.snapshot, "generation": generation,
            "policy": policy, "cost": result.cost, "edits": result.edits})
        return decision, route
