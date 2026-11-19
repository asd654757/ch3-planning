"""Paired fixed-goal MetaWorld study with shared observation and motion gates."""
import argparse
from collections import Counter
from dataclasses import asdict
import os
from pathlib import Path
import shutil
from time import perf_counter

os.environ.setdefault("MUJOCO_GL", "egl")

from ch3.execution.observed_goal import observed_goal
from ch3.execution.visual_geometry import project_point, visual_binding
from ch3.execution.visual_release import release_evidence
from ch3.repair.budgeted_recovery_fixture import RecoveryFixture, ResourceLimit
from ch3.revision.history import Clause, Contract, Event, Obligation, Snapshot, State, decide
from ch3.revision.runtime import RevisionRuntime
from scripts.benchmark_fragment_three_objects import digest, write, close
from scripts.pilot_joint_regrasp import diagnose, regrasp


ARMS = ("receipt", "no_recovery", "direct", "routed")
CASES = ("normal", "no_lift", "transient", "unobservable_no_lift")
PROTOCOL = Path("docs/ch3_fixed_goal_validation_protocol_20261119.md")


class FixedGoalTrial(RecoveryFixture):
    """Attach the existing physical gates to scoped runtime dispatch tickets."""

    def __init__(self, seed, case, output, runtime, arm, initial_plan_seconds):
        self.inject_no_lift = case in {"no_lift", "unobservable_no_lift"}
        self.runtime, self.arm = runtime, arm
        self.suffix = runtime.decision.plan
        self.commands = []
        self.routes = []
        self.planner_calls = 1  # the initial, shared Runtime.accept call
        self.planning_seconds = initial_plan_seconds
        self.regrasp_attempts = 0
        self.fault_steps = 0
        self.holding_committed = False
        self.first_evidence = None
        self.eb = []
        self.denied_place_requests = 0
        self.physical_place_calls = 0
        self.dispatch("pick", None)
        super().__init__(seed, "normal", output)
        self.blackout = 1 if case == "transient" else None if case == "unobservable_no_lift" else 0

    def _step(self, action):
        if self.phase != "terminal_scoring" and self.steps >= 900:
            raise ResourceLimit("total_control_step_cap")
        if self.inject_no_lift and self.phase == "prefix" and action[-1] > 0:
            action = action.copy()
            action[-1] = -1.0
            self.fault_steps += 1
        return super()._step(action)

    def dispatch(self, skill, target):
        before = self.runtime.snapshot
        command = self.runtime.consume(self.runtime.issue())
        assert (command.skill, command.object_id, command.target_id) == (skill, "blue_candidate", target)
        assert self.runtime.snapshot is before
        self.commands.append(command.model_dump(mode="json"))

    def reassess(self, state, obs_id, event=None):
        snapshot = self.runtime.snapshot
        history = (Event(snapshot.cursor+1, *event, observation_id=obs_id),) if event else ()
        self.runtime.observe(state, observation_id=obs_id, events=history)
        start = perf_counter()
        if self.arm == "direct":
            current = self.runtime.snapshot
            gold = decide(current, current.contract, self.runtime.horizon)
            self.planner_calls += 1
            if gold.status != "ready":
                raise ValueError("unexpected physical adapter contract conflict")
            decision, _ = self.runtime.assess(gold.plan, allow_replan=False)
            route = "DIRECT_REPLAN"
        else:
            remaining = self.suffix[1:] if event else self.suffix
            decision, route = self.runtime.assess(remaining)
            self.planner_calls += int(route != "REUSE_VALID_SUFFIX")
        self.planning_seconds += perf_counter()-start
        self.routes.append(route)
        self.suffix = decision.plan

    def _place(self):
        if "continue" not in self.allowed():
            raise ValueError("fresh physical place gate rejected")
        obs = self.latest["id"]
        self.reassess(State(blue="hand", holding="blue"), obs, ("pick", "blue", None))
        self.holding_committed = True
        self.dispatch("place", "green_region")
        self.physical_place_calls += 1
        return super()._place()

    def recover(self, diagnosis):
        """Reuse the archived guarded open/lift/regrasp macro with the same controller."""
        self.reassess(State(), diagnosis["observation_id"])
        self.regrasp_attempts += 1
        execute = self.controller.execute

        def execute_fresh(request, **options):
            # The macro calls execute only after fresh RGB target rebinding.
            if request.skill == "pick":
                self.reassess(State(), request.observation_id)
                self.dispatch("pick", None)
            return execute(request, **options)

        self.controller.execute = execute_fresh
        try:
            return regrasp(self, diagnosis)
        finally:
            self.controller.execute = execute

    def verify_release(self):
        receipt = next(e["receipt"] for e in reversed(self.events) if e["action"] == "place")
        if not receipt["completed"]:
            return False
        np = self.np
        origin = np.asarray(self.env.get_endeff_pos()).copy()
        pixels, hands, locations = [], [], []
        self.phase = "post_release_evidence"
        for index, delta in enumerate(([0., 0., .04], [.04, 0., .04])):
            target = origin + delta
            for _ in range(40):
                self.env.step(np.r_[np.clip(10*(target-self.env.get_endeff_pos()), -1, 1), -1.])
                if np.linalg.norm(self.env.get_endeff_pos()-target) < .005:
                    break
            else:
                return False
            rgb = self.env.render().copy()
            pixel = self._pixel(rgb, "blue")
            if pixel is None:
                return False
            pixels.append(pixel)
            hands.append(project_point(self.calibration, self.env.get_endeff_pos()))
            locations.append(self.xy("blue_candidate", f"release-{index}", pixel, .028))
        goal = self.xy("green_region", "initial", self.initial_green, .008)
        released = release_evidence(pixels, hands, locations[-1], goal, prior_holding_supported=self.holding_committed,
            open_retreat_completed=True, single_object_fixture=True, require_destination=False)
        arrived = observed_goal(locations, goal)
        self.eb.append({"release": released, "arrival": arrived, "target_pixels": pixels, "hand_pixels": hands})
        if released["status"] == "release_supported" and arrived["status"] == "satisfied":
            self.reassess(State(blue="green"), "verified-release-rgb", ("place", "blue", "green"))
            return not self.suffix
        return False

    def xy(self, object_id, obs_id, pixel, plane):
        return visual_binding(self.calibration, object_id=object_id, observation_id=obs_id,
                              pixel=pixel, plane_z=plane).position[:2]


def supervise(fixture):
    # Compare receipt as the internal fact source, retaining the physical gate.
    for _ in range(2):
        evidence = diagnose(fixture)
        fixture.eb.append(evidence)
        holding = "continue" in fixture.allowed()
        if fixture.first_evidence is None:
            fixture.first_evidence = holding
        if fixture.arm == "receipt" and fixture.receipt["completed"] and not holding:
            fixture.denied_place_requests += 1
            return "receipt_based_place_denied"
        if holding:
            fixture.perform("continue", 900)
            return "verified_continuation"
        if evidence["gate"]["authorized"]:
            if fixture.arm in {"receipt", "no_recovery"}:
                return "retry_disabled"
            return fixture.recover(evidence)
    return "insufficient_observation_stop"


def run(seed, case, arm, output):
    obligations = tuple(Obligation(c) for c in (Clause(id="goal", kind="at", object="blue", target="green"),
        Clause(id="empty", kind="empty"), Clause(id="protected", kind="forbid", object="yellow")))
    task = Contract(1, obligations)
    runtime = RevisionRuntime(Snapshot(Contract(0, ()), State(), (), "known-reset-fixture"))
    start = perf_counter()
    runtime.accept(task, expected_digest=runtime.snapshot.digest, expected_generation=runtime.generation)
    fixture = FixedGoalTrial(seed, case, output, runtime, arm, perf_counter()-start)
    try:
        # Private prefix geometry is retained for scoring only. No policy gets it.
        blue = fixture.env.data.body("candidate_blue").xpos.copy()
        hand = fixture.env.get_endeff_pos().copy()
        completed = False
        try:
            route = supervise(fixture)
            completed = fixture.verify_release() if fixture.physical_place_calls else False
        except ResourceLimit:
            route = "control_step_cap"
        steps = fixture.steps
        state = bool(blue[2] >= .075 and fixture.np.linalg.norm(blue-hand) <= .10)
        receipt = bool(fixture.receipt["completed"])
        first_claim = receipt if arm == "receipt" else bool(fixture.first_evidence)
        assert runtime.snapshot.contract == task
        return {"seed": seed, "case": case, "arm": arm, "route": route,
                "success": fixture.score(), "reported_success": completed,
                "initial_receipt_completed": receipt, "independent_prefix_lifted_near_hand": state,
                "first_probe_holding_claim": first_claim, "false_holding_promotion": first_claim and not state,
                "prefix_blue_z": float(blue[2]), "fault_steps": fixture.fault_steps,
                "unsupported_place_requests": fixture.denied_place_requests,
                "executed_places": fixture.physical_place_calls, "regrasp_attempts": fixture.regrasp_attempts,
                "planner_calls": fixture.planner_calls, "planning_ms": 1000*fixture.planning_seconds,
                "control_steps": steps, "routes": fixture.routes, "commands": fixture.commands,
                "observations": fixture.observations, "evidence": fixture.eb, "events": fixture.events,
                "final_contract_version": runtime.snapshot.contract.version,
                "history": [asdict(e) for e in runtime.snapshot.history],
                "episode_resets": int(fixture.env._ch3_reset_count)}
    finally:
        fixture.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--development", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    files = [Path(__file__).resolve().relative_to(Path.cwd()), PROTOCOL, Path("scripts/pilot_joint_regrasp.py"),
             Path("scripts/audit_recovery_observability.py"), Path("scripts/benchmark_fragment_three_objects.py"),
             Path("ch3/repair/budgeted_recovery_fixture.py"), *Path("ch3/revision").glob("*.py"),
             *Path("ch3/execution").glob("*.py")]
    hashes = {str(path): digest(path) for path in files}
    for path in files:
        target = args.output/"sources"/path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    seeds = [1190] if args.development else list(range(1201, 1209))
    write(args.output/"plan.json", {"seeds": seeds, "cases": CASES, "arms": ARMS,
        "development": args.development, "model_calls": 0, "physical_backend": "MetaWorld MuJoCo simulation",
        "protocol_digest": digest(PROTOCOL)})
    rows = []
    for index, seed in enumerate(seeds):
        for j, case in enumerate(CASES):
            k = (index+j) % len(ARMS)
            for arm in ARMS[k:]+ARMS[:k]:
                try:
                    row = run(seed, case, arm, args.output/f"{seed}_{case}_{arm}")
                except Exception as exc:
                    row = {"seed": seed, "case": case, "arm": arm, "error": repr(exc), "success": False}
                rows.append(row)
                write(args.output/"results.json", rows)
                print(seed, case, arm, row.get("success"), row.get("route"), row.get("error", ""), flush=True)
    assert len(rows) == len(seeds)*len(CASES)*len(ARMS)
    summary = {}
    for arm in ARMS:
        group = [r for r in rows if r["arm"] == arm]
        summary[arm] = {name: sum(row.get(name, 0) for row in group) for name in ("success", "reported_success",
            "false_holding_promotion", "unsupported_place_requests", "executed_places", "regrasp_attempts",
            "planner_calls", "control_steps")}
        summary[arm].update(errors=sum("error" in r for r in group),
            success_by_case=Counter(r["case"] for r in group if r["success"]),
            false_terminal_success=sum(r.get("reported_success", False) and not r["success"] for r in group))
    write(args.output/"summary.json", summary)
    close(args.output, hashes, extra={"images": {str(p.relative_to(args.output)): digest(p) for p in args.output.glob("*/*.png")}})
    if any("error" in r for r in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
