"""Backend-neutral routing checks, not plan validation or motion safety checks.

Bindings must be supplied by a separate perception/calibration adapter. This
module neither infers them nor converts image boxes into robot coordinates.
"""

from dataclasses import dataclass
from math import isfinite
from typing import Literal

from ch3.compiler.executable_plan import ExecutableStep


@dataclass(frozen=True)
class TargetBinding:
    object_id: str
    position: tuple[float, float, float]
    coordinate_frame: str
    observation_id: str
    source: Literal["visual_estimate", "simulator_truth"]


@dataclass(frozen=True)
class SkillRequest:
    step_id: int
    skill: str
    arm: str
    object_binding: TargetBinding
    target_binding: TargetBinding | None
    backend: str
    observation_id: str


@dataclass(frozen=True)
class BackendContract:
    backend: str
    skills: frozenset[str]
    arms: frozenset[str]
    coordinate_frame: str
    allowed_binding_sources: frozenset[str] = frozenset({"visual_estimate"})

    def request(self, step: ExecutableStep, *, bindings: dict[str, TargetBinding],
                observation_id: str) -> SkillRequest:
        """Refuse unbound, stale and privilege-incompatible target requests.

        Callers still need Validator/Goal Checker and backend-specific geometry,
        reachability and collision checks. No execution is performed here.
        """
        if step.source_skill not in self.skills or step.args.get("arm") not in self.arms:
            raise ValueError("unsupported skill or arm")
        if not observation_id:
            raise ValueError("missing observation identity")

        def resolve(identifier):
            binding = bindings.get(identifier)
            if binding is None or binding.object_id != identifier:
                raise ValueError("missing or mismatched target binding")
            if binding.observation_id != observation_id:
                raise ValueError("stale target binding")
            if binding.coordinate_frame != self.coordinate_frame:
                raise ValueError("coordinate frame mismatch")
            if binding.source not in self.allowed_binding_sources:
                raise ValueError("privileged or unsupported binding source")
            if len(binding.position) != 3 or not all(isfinite(x) for x in binding.position):
                raise ValueError("invalid target position")
            return binding

        obj = resolve(step.args.get("object_id"))
        target_id = step.args.get("target_id")
        if step.source_skill in {"place", "push"} and not target_id:
            raise ValueError("skill requires a destination binding")
        target = resolve(target_id) if target_id else None
        return SkillRequest(step.step_id, step.source_skill, step.args["arm"],
                            obj, target, self.backend, observation_id)
