"""Finite-horizon recovery selection; advisory only, never execution evidence.

The supplied model must be estimated separately from evaluation trajectories.
This module neither queries a simulator nor learns/guesses missing probabilities.
Hypotheses describe only the recovery-relevant, explicitly modelled state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import isclose, isfinite
from typing import Callable


ACTIONS = ("continue", "observe", "retry", "replan", "stop")


@dataclass(frozen=True)
class Budget:
    observations: int
    commands: int
    model_calls: int
    time_units: int

    def __post_init__(self):
        if any(type(v) is not int or v < 0 for v in self.values()):
            raise ValueError("budgets must be nonnegative integers")

    def values(self):
        return (self.observations, self.commands, self.model_calls, self.time_units)

    def spend(self, cost: Budget) -> Budget | None:
        remaining = tuple(a - b for a, b in zip(self.values(), cost.values()))
        return Budget(*remaining) if min(remaining) >= 0 else None


@dataclass(frozen=True)
class Branch:
    probability: float
    observation: str
    next_state: str | None = None
    terminal: str | None = None
    resources: Budget | None = None

    def __post_init__(self):
        if not isfinite(self.probability) or not 0 < self.probability <= 1:
            raise ValueError("branch probability must be in (0, 1]")
        if not self.observation:
            raise ValueError("observable outcome label required")
        if self.terminal not in {None, "success", "failure"}:
            raise ValueError("invalid terminal label")
        if (self.next_state is None) == (self.terminal is None):
            raise ValueError("exactly one of next_state and terminal required")


@dataclass(frozen=True)
class ActionModel:
    resources: Budget
    loss: float
    # Omitted states make this action inadmissible for beliefs including them.
    transitions: dict[str, tuple[Branch, ...]]

    def __post_init__(self):
        if not isfinite(self.loss) or self.loss < 0:
            raise ValueError("finite nonnegative action loss required")
        if self.resources == Budget(0, 0, 0, 0):
            raise ValueError("each action must consume a resource")
        for branches in self.transitions.values():
            if not branches or not isclose(sum(b.probability for b in branches), 1, abs_tol=1e-9):
                raise ValueError("transition probabilities must sum to one")
            # A sensor label may not silently encode hidden terminal status.
            labels = {}
            for branch in branches:
                if branch.resources is not None and self.resources.spend(branch.resources) is None:
                    raise ValueError("branch resource use exceeds admission cap")
                if branch.observation in labels and labels[branch.observation] != branch.terminal:
                    raise ValueError("ambiguous terminal observation")
                labels[branch.observation] = branch.terminal


@dataclass(frozen=True)
class RecoveryModel:
    actions: dict[str, ActionModel]
    # Expected continuation value at the search boundary, not observed success.
    # Zero is a deliberately pessimistic alternative, but must be declared.
    leaf_success: dict[str, float]
    provenance: str
    leaf_provenance: str
    # Shared PUBLIC evidence-label projection for hypothetical search only.
    # Runtime updates still require freshly checked actual guard permissions.
    observation_permissions: dict[str, frozenset[str]] = field(default_factory=dict)
    # Declared lower bound on resources needed beyond the search boundary.
    leaf_min_resources: dict[str, Budget] = field(default_factory=dict)

    def __post_init__(self):
        if not self.provenance.strip() or not self.leaf_provenance.strip():
            raise ValueError("model and horizon-value provenance required")
        if any(mask - set(ACTIONS) for mask in self.observation_permissions.values()):
            raise ValueError("invalid observation permission projection")
        if self.leaf_min_resources.keys() - self.leaf_success.keys():
            raise ValueError("unknown leaf resource state")
        if not self.leaf_success or any(not isfinite(p) or not 0 <= p <= 1
                                       for p in self.leaf_success.values()):
            raise ValueError("leaf success estimates must be in [0, 1]")
        for name, action in self.actions.items():
            if name not in ACTIONS[:-1]:
                raise ValueError("unsupported action")
            if set(action.transitions) - self.leaf_success.keys():
                raise ValueError("unknown transition source")
            labels = {}
            for branches in action.transitions.values():
                for branch in branches:
                    if branch.next_state is not None and branch.next_state not in self.leaf_success:
                        raise ValueError("unknown transition destination")
                    if branch.observation in labels and labels[branch.observation] != branch.terminal:
                        raise ValueError("outcome labels must expose terminal status consistently")
                    labels[branch.observation] = branch.terminal


@dataclass(frozen=True)
class Context:
    observation_id: str
    belief: dict[str, float]
    budget: Budget
    # These permissions must come from the common evidence/contract guard.
    # Spending budget or obtaining a predicted success NEVER grants permission.
    enabled: frozenset[str]

    def __post_init__(self):
        if not self.observation_id or not self.belief:
            raise ValueError("observation binding and nonempty belief required")
        if any(not isfinite(p) or p < 0 for p in self.belief.values()):
            raise ValueError("invalid belief probability")
        if not isclose(sum(self.belief.values()), 1, abs_tol=1e-9):
            raise ValueError("belief must sum to one")
        if self.enabled - set(ACTIONS):
            raise ValueError("invalid action permission")


@dataclass(frozen=True)
class Decision:
    action: str
    observation_id: str
    values: dict[str, float]
    reason: str
    model_provenance: str
    depth: int


class RecoveryDecisionPlanner:
    """Exact short lookahead over observable outcomes, with Bayesian updates.

    Success reward is 1, failure/stop reward is 0; costs are in those units.
    Values are model predictions, NOT evidence of physical completion. Common
    guards retain authority. A declared public evidence-label projection may
    update hypothetical permissions; it never updates actual execution guards.
    """

    def __init__(self, model: RecoveryModel, *, depth: int = 2):
        if type(depth) is not int or not 1 <= depth <= 4:
            raise ValueError("depth must be an integer in [1, 4]")
        self.model, self.depth = model, depth

    def admissible(self, context: Context) -> tuple[str, ...]:
        support = {s for s, p in context.belief.items() if p > 0}
        if not support <= self.model.leaf_success.keys():
            return ("stop",)
        return tuple(a for a in ACTIONS[:-1] if a in context.enabled
                     and a in self.model.actions
                     and support <= self.model.actions[a].transitions.keys()
                     and context.budget.spend(self.model.actions[a].resources) is not None) + ("stop",)

    def outcomes(self, context: Context, action: str) -> tuple[tuple[str, float, str | None, Context | None], ...]:
        """Predict observable branches; never expose the sampled latent state."""
        if action == "stop" or action not in self.admissible(context):
            raise ValueError("action must be admissible and nonterminal")
        spec = self.model.actions[action]
        groups = {}
        for state, prior in context.belief.items():
            if prior == 0:
                continue
            for branch in spec.transitions[state]:
                # Actual duration is observable and may affect both posterior
                # and remaining budget. Do not replace it with the admission cap.
                key = (branch.observation, branch.terminal, branch.resources or spec.resources)
                mass, states = groups.setdefault(key, [0.0, {}])
                weight = prior * branch.probability
                groups[key][0] = mass + weight
                if branch.next_state is not None:
                    states[branch.next_state] = states.get(branch.next_state, 0) + weight
        result = []
        for (label, terminal, resources), (mass, states) in sorted(
                groups.items(), key=lambda item: (item[0][0], item[0][1] or "", item[0][2].values())):
            remaining = context.budget.spend(resources)
            posterior = None if terminal else Context(
                context.observation_id + "/predicted:" + label,
                {s: p / mass for s, p in states.items()}, remaining,
                self.model.observation_permissions.get(label, context.enabled))
            result.append((label, mass, terminal, posterior))
        return tuple(result)

    def _values(self, context: Context, depth: int,
                continuation: Callable[[Context, tuple[str, ...]], str] | None = None) -> dict[str, float]:
        values = {"stop": 0.0}
        for action in self.admissible(context):
            if action == "stop":
                continue
            value = -self.model.actions[action].loss
            for _, probability, terminal, posterior in self.outcomes(context, action):
                if terminal:
                    future = float(terminal == "success")
                elif depth == 1:
                    # No free success after all physical resources are exhausted.
                    future = (sum(p * self.model.leaf_success[s]
                                  for s, p in posterior.belief.items()
                                  if posterior.budget.spend(self.model.leaf_min_resources.get(s, Budget(0,0,0,0))) is not None)
                              if self.admissible(posterior) != ("stop",) else 0.0)
                else:
                    branch_values = self._values(posterior, depth - 1, continuation)
                    selected = (continuation(posterior, self.admissible(posterior))
                                if continuation else self._best(branch_values))
                    if selected not in branch_values:
                        raise ValueError("comparison policy returned inadmissible action")
                    future = branch_values[selected]
                value += probability * future
            values[action] = value
        return values

    def update(self, context: Context, action: str, *, observation: str,
               observation_id: str, enabled: frozenset[str], actual_cost: Budget | None = None) -> Context:
        """Condition on an ACTUALLY acquired observation, not a predicted branch.

        Caller supplies a fresh observation binding and freshly checked guard
        permissions. Terminal model labels cannot authorize task completion.
        """
        if not observation_id or observation_id == context.observation_id:
            raise ValueError("fresh actual observation id required")
        matches = []
        for label, _, terminal, posterior in self.outcomes(context, action):
            if posterior is not None and label == observation:
                if actual_cost is None or context.budget.spend(actual_cost) == posterior.budget:
                    matches.append(posterior)
            if terminal is not None:
                # Terminal evidence remains the independent supervisor's job.
                continue
        if len(matches) == 1:
            posterior = matches[0]
            return Context(observation_id, posterior.belief, posterior.budget, enabled)
        if len(matches) > 1:
            raise ValueError("ambiguous observation duration; actual_cost required")
        raise ValueError("unmodelled or terminal observation; obtain evidence and stop/rebuild model")

    @staticmethod
    def _best(values: dict[str, float]) -> str:
        # Stop on a zero-gain tie; positive ties follow a documented fixed order.
        if max(values.values()) <= 0:
            return "stop"
        return max((a for a in ACTIONS if a in values), key=values.__getitem__)

    def choose(self, context: Context) -> Decision:
        values = self._values(context, self.depth)
        action = self._best(values)
        reason = ("unsupported_hypothesis" if any(p > 0 and s not in self.model.leaf_success
                                                  for s, p in context.belief.items()) else
                  "no_admissible_recovery" if len(values) == 1 else
                  "no_positive_model_value" if action == "stop" else "maximum_model_value")
        return Decision(action, context.observation_id, values, reason,
                        self.model.provenance, self.depth)

    def evaluate_policy(self, context: Context,
                        policy: Callable[[Context, tuple[str, ...]], str]) -> float:
        """Same model, branches, permissions, resources and horizon for controls."""
        values = self._values(context, self.depth, policy)
        action = policy(context, self.admissible(context))
        if action not in values:
            raise ValueError("comparison policy returned inadmissible action")
        return values[action]
