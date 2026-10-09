"""Online semantic supervision. Predictions never update observed progress.

The backend owns motion/control. The observer owns evidence extraction. Neither
command acknowledgement nor symbolic simulation constitutes goal evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import re
from typing import Protocol
from uuid import uuid4

from ch3.compiler.executable_plan import ExecutableStep, compile_plan
from ch3.goal.goal_checker import goal_satisfied
from ch3.repair.persistent_model_recovery import strict_plan
from ch3.schema.model_plan import GoalSpec, ModelPlan
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator


class Truth(str, Enum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Evidence:
    value: Truth
    source: str
    detail: str = ""

    def __post_init__(self):
        if not isinstance(self.value, Truth) or not self.source.strip():
            raise ValueError("typed truth and evidence source required")
        if self.source in {"command", "prediction", "scoring_truth"}:
            raise ValueError("command/prediction/scoring truth is not observation evidence")


@dataclass
class Observation:
    episode_id: str
    sequence: int
    state: WorldState
    # Presence means occupancy was observed, including explicit None for empty.
    occupancy: dict[str, str | None]
    goal_evidence: dict[str, Evidence] = field(default_factory=dict)
    images: tuple[str, ...] = ()


@dataclass(frozen=True)
class Task:
    instruction: str
    goal_facts: tuple[str, ...]
    forbidden_objects: frozenset[str] = frozenset()

    def __post_init__(self):
        if not self.instruction.strip() or not self.goal_facts:
            raise ValueError("instruction and grounded goal facts required")
        GoalSpec(facts=list(self.goal_facts))


@dataclass(frozen=True)
class Receipt:
    # Backend status, not proof of task completion.
    status: str
    detail: str = ""

    def __post_init__(self):
        if self.status not in {"success", "failed", "unknown"}:
            raise ValueError("invalid execution receipt status")


class Planner(Protocol):
    def generate(self, request: dict, images: tuple[str, ...]) -> str: ...


class PolicyAdapter(Protocol):
    def execute(self, step: ExecutableStep, *, instruction: str,
                context: dict, command_id: str) -> Receipt: ...


class Supervisor:
    """Single-episode sequential controller with bounded model calls.

    Each dispatch consumes one validated action. A fresh post-action observation
    is mandatory before another dispatch. Rejected candidates never reach a
    policy; remaining plans are revalidated against every fresh observation.
    """

    def __init__(self, task: Task, validator: Validator, planner: Planner, *,
                 attempts: int = 2, max_model_calls: int = 20,
                 max_commands: int = 100):
        if attempts < 1 or max_model_calls < 1 or max_commands < 1:
            raise ValueError("positive budgets required")
        self.task, self.validator, self.planner = task, validator, planner
        self.attempts, self.max_model_calls = attempts, max_model_calls
        self.max_commands = max_commands
        self.model_calls = 0
        self._observation: Observation | None = None
        self._plan: ModelPlan | None = None
        self._plan_sequence: int | None = None
        self._awaiting_observation = False
        self._dispatching = False
        self._history: list[dict] = []
        self.audit: list[dict] = []
        self.last_rejection: dict | None = None
        self.status = "need_observation"

    @property
    def history(self) -> tuple[dict, ...]:
        # Defensive deep copy: callers cannot rewrite executed history.
        return tuple(json.loads(json.dumps(self._history)))

    @property
    def remaining_goals(self) -> tuple[str, ...]:
        obs = self._observation
        return tuple(f for f in self.task.goal_facts if obs is None or
                     f not in obs.goal_evidence or obs.goal_evidence[f].value != Truth.TRUE)

    def observe(self, observation: Observation) -> None:
        if self._dispatching:
            raise ValueError("cannot observe during dispatch")
        old = self._observation
        if not observation.episode_id or observation.sequence < 0:
            raise ValueError("episode and nonnegative sequence required")
        if old and (observation.episode_id != old.episode_id or
                    observation.sequence <= old.sequence):
            raise ValueError("newer observation from same episode required")
        state = observation.state
        arms = self.validator.registry.arms
        if state.objects != self.validator.scene_objects:
            raise ValueError("object grounding changed; build a new supervisor")
        if set(observation.occupancy) - arms or set(state.holding) - arms:
            raise ValueError("unregistered arm")
        held = list(state.holding.values())
        if len(held) != len(set(held)) or set(held) - state.objects:
            raise ValueError("inconsistent held objects")
        for arm, obj in observation.occupancy.items():
            if state.holding.get(arm) != obj:
                raise ValueError("occupancy conflicts with observed state")
        if set(state.holding) - set(observation.occupancy):
            raise ValueError("held object requires explicit occupancy evidence")
        if set(state.at) - state.objects or set(state.pressed | state.pushed) - state.objects:
            raise ValueError("unregistered state object")
        if set(state.at.values()) - self.validator.valid_targets:
            raise ValueError("unregistered state target")
        facts = state.facts() | {f"hand_empty({a})" for a, o in observation.occupancy.items() if o is None}
        for fact, evidence in observation.goal_evidence.items():
            if fact not in self.task.goal_facts:
                raise ValueError("evidence for undeclared goal")
            if (evidence.value == Truth.TRUE and fact not in facts or
                    evidence.value == Truth.FALSE and fact in facts):
                raise ValueError("goal evidence conflicts with observed state")
        self._observation = Observation(observation.episode_id, observation.sequence,
            state.copy(), dict(observation.occupancy), dict(observation.goal_evidence),
            tuple(observation.images))
        self._awaiting_observation = False
        self._plan_sequence = None
        self.status = "complete" if not self.remaining_goals else "ready"

    def _known_state(self) -> bool:
        obs = self._observation
        return bool(obs and set(obs.occupancy) == self.validator.registry.arms and
                    obs.state.objects <= (set(obs.state.at) | set(obs.state.holding.values())))

    def _check(self, plan: ModelPlan) -> dict | None:
        if any(a.object_id in self.task.forbidden_objects for a in plan.actions):
            return {"reason": "forbidden_object"}
        result = self.validator.validate(plan, self._observation.state.copy())
        if not result.valid:
            return {"reason": "validator", "layer": result.layer,
                    "error_code": str(result.error_code), "message": result.message,
                    "first_invalid_step": result.first_invalid_step}
        # Full goal, including previously verified facts: prevent planned regression.
        if not goal_satisfied(result.final_state, GoalSpec(facts=list(self.task.goal_facts)),
                              self.validator.registry.arms):
            return {"reason": "predicted_goal_incomplete"}
        return None

    def prepare(self) -> str:
        if self._dispatching or self._awaiting_observation or self._observation is None:
            self.status = "need_observation"
            return self.status
        if not self.remaining_goals:
            self._plan = None
            self.status = "complete"
            return self.status
        if not self._known_state():
            self._plan = None
            self.status = "need_observation"
            return self.status
        # State already predicts completion but lacks independent confirmation.
        # Request evidence instead of generating unnecessary, potentially harmful motion.
        if goal_satisfied(self._observation.state, GoalSpec(facts=list(self.task.goal_facts)),
                          self.validator.registry.arms):
            self._plan = None
            self.status = "need_observation"
            return self.status
        if self._plan is not None:
            rejection = self._check(self._plan)
            if rejection is None:
                self._plan_sequence = self._observation.sequence
                self.status = "ready"
                return self.status
            self.last_rejection = rejection
            self._plan = None
        for _ in range(self.attempts):
            if self.model_calls >= self.max_model_calls:
                break
            request = self._request()
            self.model_calls += 1
            record = {"call": self.model_calls, "request": request, "accepted": False}
            try:
                text = self.planner.generate(request, self._observation.images)
                record["output"] = text
                candidate = strict_plan(text)
                # Legacy schema permits coercion; supervisor boundary requires exact types.
                raw = text.strip()
                fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", raw, re.S)
                if fence:
                    raw = fence.group(1)
                for item in json.loads(raw)["actions"]:
                    if type(item.get("step_id")) is not int or any(
                        not isinstance(item.get(k), str) for k in ("skill", "object_id", "arm")
                    ) or (item.get("target_id") is not None and not isinstance(item["target_id"], str)):
                        raise ValueError("exact action field types required")
                rejection = self._check(candidate)
                if rejection is None:
                    self._plan = candidate.model_copy(deep=True)
                    self._plan_sequence = self._observation.sequence
                    record["accepted"] = True
                    self.audit.append(record)
                    self.last_rejection = None
                    self.status = "ready"
                    return self.status
                self.last_rejection = rejection
            except Exception as exc:
                # Transport exception strings can contain credentials/service data.
                self.last_rejection = {"reason": "generation_error", "type": type(exc).__name__}
            record["rejection"] = dict(self.last_rejection)
            self.audit.append(record)
        self.status = "safe_stop"
        return self.status

    def _request(self) -> dict:
        obs = self._observation
        return {
            "instruction": self.task.instruction,
            "episode_id": obs.episode_id, "observation_sequence": obs.sequence,
            "current_observed_facts": sorted(obs.state.facts() | obs.state.empty_hand_facts(obs.occupancy)),
            "held_objects": dict(obs.occupancy),
            "goal_facts": list(self.task.goal_facts),
            "remaining_goal_facts": list(self.remaining_goals),
            "goal_evidence": {f: {"value": e.value.value, "source": e.source, "detail": e.detail}
                              for f, e in obs.goal_evidence.items()},
            "objects": sorted(obs.state.objects),
            "targets": sorted(self.validator.valid_targets),
            "skills": sorted(self.validator.registry.skills),
            "arms": sorted(self.validator.registry.arms),
            "required_args": {s: self.validator.registry.required_args(s)
                              for s in sorted(self.validator.registry.skills)},
            "forbidden_objects": sorted(self.task.forbidden_objects),
            "executed_history_not_current_state": list(self.history),
            "rejection_feedback": self.last_rejection,
            "output_schema": {"actions": [{"step_id": "contiguous integer from 1",
                "skill": "registered skill", "object_id": "registered object",
                "target_id": "when required", "arm": "registered arm"}]},
        }

    def execute_next(self, adapter: PolicyAdapter) -> Receipt:
        if (self._dispatching or self._awaiting_observation or self.status != "ready" or
                self._plan is None or self._plan_sequence != self._observation.sequence):
            raise RuntimeError("fresh observation and validated plan required")
        if len(self._history) >= self.max_commands:
            self.status = "safe_stop"
            raise RuntimeError("execution budget exhausted")
        # Compile only the accepted plan at this private execution boundary.
        step = compile_plan(self._plan, self.validator.registry).steps[0]
        action = self._plan.actions[0].model_dump(mode="json")
        command_id = uuid4().hex
        self._dispatching = True
        self._awaiting_observation = True
        try:
            receipt = adapter.execute(step, instruction=self.task.instruction,
                context=self._request(), command_id=command_id)
            if not isinstance(receipt, Receipt):
                raise TypeError("backend must return Receipt")
        except Exception as exc:
            receipt = Receipt("unknown", type(exc).__name__)
        finally:
            self._dispatching = False
        self._history.append({"command_id": command_id,
            "observation_sequence": self._observation.sequence, "action": action,
            "receipt": {"status": receipt.status, "detail": receipt.detail}})
        remaining = [a.model_copy(update={"step_id": i + 1}, deep=True)
                     for i, a in enumerate(self._plan.actions[1:])]
        self._plan = ModelPlan(actions=remaining) if remaining and receipt.status == "success" else None
        if receipt.status != "success":
            self.last_rejection = {"reason": "execution_feedback", "status": receipt.status,
                                   "detail": receipt.detail, "action": action}
        self._plan_sequence = None
        self.status = "need_observation"
        return receipt
