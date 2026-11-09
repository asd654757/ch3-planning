"""Exact budgeted recovery with explicit positive evidence dependencies.

Research prototype. Relevance slicing is an exact state abstraction under the
declared model, not a new general POMDP result or a physical safety guarantee.
Evidence tokens are model-level valid-current propositions, not LLM assertions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite

from ch3.repair.recovery_decision import Budget, RecoveryModel


@dataclass(frozen=True)
class EvidenceContract:
    requires: frozenset[str] = frozenset()
    invalidates: frozenset[str] = frozenset()
    # Only external observation labels may produce positive evidence tokens.
    produces: dict[str, frozenset[str]] = field(default_factory=dict)

    def update(self, current, observation):
        return (current-self.invalidates) | self.produces.get(observation,frozenset())


@dataclass(frozen=True)
class CertificateDecision:
    action: str
    value: float
    action_values: dict[str,float]


class CertificateRecoveryPlanner:
    """Finite-horizon exact expected-success DP with multi-resource budgets.

    Two controls use the identical model: full certificate-state memoization and
    memoization after future-requirement slicing. Uncached mode is a test oracle.
    Slice calculation ignores evidence requirements, so it over-approximates
    reachable actions. It must never discard a potentially required certificate.
    """
    def __init__(self, model: RecoveryModel, contracts: dict[str,EvidenceContract], *,
                 depth=4, slice_evidence=True, memoize=True):
        if set(model.actions)!=set(contracts):
            raise ValueError("one explicit evidence contract per action required")
        if type(depth) is not int or not 1 <= depth <= 12:
            raise ValueError("depth must be in [1,12]")
        if any(a.loss != 0 for a in model.actions.values()):
            raise ValueError("prototype optimizes completion probability, not weighted utility")
        self.model,self.contracts,self.depth=model,contracts,depth
        self.slice_evidence,self.memoize=slice_evidence,memoize
        self.cache={}
        self.relevance_cache={}
        self.expanded=0
        self.cache_hits=0
        self.relevance_work=0

    @staticmethod
    def _belief_key(belief):
        return tuple(sorted((s,p) for s,p in belief.items() if p>0))

    def _relevant(self, support, budget, depth):
        key=(frozenset(support),budget,depth)
        if key in self.relevance_cache:
            return self.relevance_cache[key]
        relevant=set()
        reachable=set(support)
        for _ in range(depth):
            following=set()
            for name,action in self.model.actions.items():
                if budget.spend(action.resources) is None:
                    continue
                sources=reachable & action.transitions.keys()
                if not sources:
                    continue
                self.relevance_work+=1
                relevant.update(self.contracts[name].requires)
                for s in sources:
                    following.update(b.next_state for b in action.transitions[s] if b.next_state is not None)
            reachable=following
            if not reachable:
                break
        result=frozenset(relevant)
        self.relevance_cache[key]=result
        return result

    def _solve(self,belief,evidence,budget,depth):
        if depth==0:
            return CertificateDecision("stop",0.,{"stop":0.})
        support={s for s,p in belief.items() if p>0}
        if not support <= self.model.leaf_success.keys():
            return CertificateDecision("stop",0.,{"stop":0.})
        if self.slice_evidence:
            evidence=evidence & self._relevant(support,budget,depth)
        key=(self._belief_key(belief),frozenset(evidence),budget,depth)
        if self.memoize and key in self.cache:
            self.cache_hits+=1
            return self.cache[key]
        self.expanded+=1
        values={"stop":0.}
        for name,action in sorted(self.model.actions.items()):
            contract=self.contracts[name]
            if (not contract.requires <= evidence or not support <= action.transitions.keys()
                    or budget.spend(action.resources) is None):
                continue
            groups={}
            terminal=0.
            for s,prior in belief.items():
                if prior<=0:
                    continue
                for branch in action.transitions[s]:
                    mass=prior*branch.probability
                    if branch.terminal:
                        terminal+=mass*float(branch.terminal=="success")
                    else:
                        cost=branch.resources or action.resources
                        group=groups.setdefault((branch.observation,cost),{})
                        group[branch.next_state]=group.get(branch.next_state,0)+mass
            value=terminal
            for (observation,cost),distribution in groups.items():
                mass=sum(distribution.values())
                posterior={s:p/mass for s,p in distribution.items()}
                new_evidence=contract.update(evidence,observation)
                child=self._solve(posterior,new_evidence,budget.spend(cost),depth-1)
                value+=mass*child.value
            values[name]=value
        # Stop on a zero-value tie. Positive ties have a fixed alphabetical rule.
        selected="stop" if max(values.values())<=0 else max(sorted(values),key=values.__getitem__)
        result=CertificateDecision(selected,values[selected],values)
        if self.memoize:
            self.cache[key]=result
        return result

    def choose(self,belief,evidence,budget):
        if not belief or any(not isfinite(p) or p<0 for p in belief.values()) or abs(sum(belief.values())-1)>1e-9:
            raise ValueError("normalized finite belief required")
        return self._solve(belief,frozenset(evidence),budget,self.depth)
