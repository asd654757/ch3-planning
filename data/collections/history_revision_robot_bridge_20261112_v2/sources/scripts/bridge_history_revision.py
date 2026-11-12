"""Three physical prefix trials -> RGB evidence -> semantic revision replay.

This is an integration check, not an LLM robot benchmark. Revision patches are
scripted fixtures. No controller receipt alone becomes a confirmed place event.
"""
import argparse
from dataclasses import asdict, is_dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil

os.environ.setdefault("MUJOCO_GL", "egl")

from ch3.execution.observed_goal import observed_goal
from ch3.execution.visual_geometry import project_point, visual_binding
from ch3.execution.visual_release import release_evidence
from ch3.repair.budgeted_recovery_fixture import RecoveryFixture
from ch3.revision.cases import json_default
from ch3.revision.diagnosis import diagnose
from ch3.revision.history import Clause, Contract, Edit, Event, Obligation, Patch, Snapshot, State
from ch3.revision.session import RevisionSession


def write(path, value):
    def encode(item):
        return asdict(item) if is_dataclass(item) else json_default(item)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=encode)+"\n")


def run(seed, out):
    from PIL import Image
    fixture = None
    row = {"seed": seed, "status": "error", "physical_prefixes": 1, "model_calls": 0,
           "semantic_updates_source": "scripted_gold_integration_not_model_output"}
    try:
        fixture = RecoveryFixture(seed, "normal", out)
        fixture.blackout = 0
        reference = visual_binding(fixture.calibration, object_id="green_region", observation_id="initial",
            pixel=fixture.initial_green, plane_z=.008).position[:2]
        fixture.perform("observe", 1000)
        if "continue" not in fixture.allowed():
            raise ValueError("fresh_holding_not_supported")
        holding_id = fixture.latest["id"]
        fixture.perform("continue", 1000)
        place_receipt = [e["receipt"] for e in fixture.events if e["action"] == "place"][-1]
        if not place_receipt["completed"]:
            raise ValueError("place_controller_incomplete_no_history_confirmation")
        # Observe detachment and placement independently of the command receipt.
        origin = fixture.np.asarray(fixture.env.get_endeff_pos()).copy()
        records, positions = [], []
        fixture.phase = "post_release_evidence"
        for i, delta in enumerate(([0., 0., .04], [.04, 0., .04])):
            target = origin+delta
            reached = False
            for _ in range(40):
                fixture.env.step(fixture.np.r_[fixture.np.clip(10*(target-fixture.env.get_endeff_pos()), -1, 1), -1.])
                reached = fixture.np.linalg.norm(fixture.env.get_endeff_pos()-target) < .005
                if reached:
                    break
            if not reached:
                raise ValueError("open_retreat_incomplete")
            rgb = fixture.env.render().copy()
            Image.fromarray(fixture.np.flip(rgb, (0, 1))).save(out/f"release_{i}.png")
            pixel = fixture._pixel(rgb, "blue")
            records.append({"target_pixel": pixel,
                            "hand_pixel": project_point(fixture.calibration, fixture.env.get_endeff_pos())})
            if pixel is None:
                raise ValueError("post_release_target_not_visible")
            positions.append(visual_binding(fixture.calibration, object_id="blue", observation_id=f"release-{i}",
                pixel=pixel, plane_z=.028).position[:2])
        released = release_evidence([r["target_pixel"] for r in records], [r["hand_pixel"] for r in records],
            positions[-1], reference, prior_holding_supported=True, open_retreat_completed=True,
            single_object_fixture=True, require_destination=False)
        goal = observed_goal(positions, reference)
        row.update(release=released, goal=goal, records=records)
        if released["status"] != "release_supported" or goal["status"] != "satisfied":
            raise ValueError("RGB_release_or_arrival_not_supported")
        ledger = (Event(1, "pick", "blue", None, observation_id=holding_id),
                  Event(2, "place", "blue", "green", observation_id="post-release-RGB"))
        va = Clause(id="green_visit", kind="visit", object="blue", target="green")
        vb = Clause(id="table_visit", kind="visit", object="blue", target="table")
        empty = Clause(id="empty", kind="empty")
        s = Snapshot(Contract(0, tuple(Obligation(c) for c in (va, vb, empty))),
            State("green"), ledger, "post-release-RGB").validate()
        session = RevisionSession(s)
        text = "原来的两项必经放置必须先回桌面，再进绿色区，按原记录判断，不重做。"
        order = Clause(id="order", kind="before", first="table_visit", second="green_visit")
        first = session.submit(Patch(version=0, snapshot=s.digest,
            edits=[Edit(op="add", id="order", quote=text, clause=order)]), text)
        before = fixture.steps
        blocked = False
        try:
            _ = session.executable_contract
        except ValueError:
            blocked = True
        certificate = diagnose(s, first.proposed)
        pending = session.working_snapshot
        followup = "取消新增顺序和回桌面的要求，从现在起禁止再操作蓝块，最后保持空手。"
        edits = [Edit(op="remove", id=id, quote=followup) for id in ("order", "table_visit")]
        edits.append(Edit(op="add", id="protect", quote=followup,
                          clause=Clause(id="protect", kind="forbid", object="blue")))
        second = session.submit(Patch(version=1, snapshot=pending.digest, edits=edits), followup)
        _ = session.executable_contract
        row.update(status="complete", initial_snapshot=asdict(s),
            conflicting_revision=asdict(first), conflict_certificate=certificate,
            followup_revision=asdict(second), old_task_execution_blocked=blocked,
            control_steps_during_semantic_revision=fixture.steps-before,
            history_preserved=session.active.history == ledger,
            semantic_bridge_pass=(first.decision.status == "conflict" and second.decision.status == "ready"
                and blocked and session.active.history == ledger and not second.decision.plan),
            control_steps_before_terminal_score=fixture.steps, events=fixture.events,
            observations=fixture.observations, episode_resets=fixture.env._ch3_reset_count)
        # Independent terminal truth score only AFTER all decisions. No callback follows.
        row["independent_physical_goal_success"] = fixture.score()
    except Exception as exc:
        row["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        if fixture is not None:
            fixture.close()
    return row


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    sources = [Path(__file__).resolve().relative_to(Path.cwd()),
        *sorted(Path("ch3/revision").glob("*.py")), Path("ch3/repair/budgeted_recovery_fixture.py"),
        *[Path("ch3/execution")/f for f in ("observed_goal.py", "visual_release.py", "visual_holding.py",
                                           "visual_geometry.py", "fixed_pickplace.py", "multiobject_scene.py")]]
    hashes = {}
    for source in sources:
        hashes[str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
        dest = args.output/"sources"/source
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    seeds = [1110, 1111, 1112]
    write(args.output/"manifest.json", {"seeds": seeds, "source_hashes": hashes,
        "physical_prefix_trials": 3, "model_calls": 0, "revisions": "scripted_integration_fixtures",
        "purpose": "RGB_evidence_to_history_and_pending_revision_bridge_not_method_comparison"})
    rows = []
    for seed in seeds:
        row = run(seed, args.output/f"seed_{seed}")
        rows.append(row)
        write(args.output/"results.json", rows)
        print(json.dumps({k: row.get(k) for k in ("seed", "status", "semantic_bridge_pass", "independent_physical_goal_success", "error")} ), flush=True)
    write(args.output/"closeout.json", {"sources_unchanged": all(
        hashlib.sha256(Path(s).read_bytes()).hexdigest() == digest for s, digest in hashes.items())})


if __name__ == "__main__":
    main()
