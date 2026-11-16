"""Author-built bounded planning cases; fixed accepted tasks, not NL translation.

The 20 validation instances combine known mechanisms in the SAME two-object
domain. They are not new domains or independent evidence of open-world transfer.
Background records are legal synthetic past actions, not physical robot logs.
"""
from dataclasses import replace
from hashlib import sha256
import json

from ch3.revision.history import Clause, Contract, Event, Obligation, Snapshot, State, successors


def clause(id, kind, *, scope="past", **fields):
    return scope, Clause(id=id, kind=kind, **fields)


def at(obj, target):
    return clause("goal_"+obj, "at", object=obj, target=target)


def visit(obj, target, *, scope="past", id=None):
    return clause(id or "visit_"+obj, "visit", object=obj, target=target, scope=scope)


def order(first="visit_blue", second="visit_yellow"):
    return clause("order", "before", first=first, second=second)


def protect(obj, *, scope="now"):
    return clause("protect_"+obj, "forbid", object=obj, scope=scope)


EMPTY = clause("empty", "empty")


def make_case(index, split, family, operations, obligations, status, *, unknown=(), background=4):
    state, history = State(), []
    def action(kind, obj, target=None):
        nonlocal state
        seq = len(history)+1
        state, event = next((ns, e) for ns, e in successors(state, seq)
                           if (e.kind, e.object, e.target) == (kind, obj, target))
        history.append(replace(event, observation_id="obs-"+sha256(
            f"{split}/{index}/{seq}".encode()).hexdigest()[:16]))
    def move(obj, target):
        action("pick", obj)
        action("place", obj, target)
    for _ in range(background):
        move("yellow", "return")
        move("yellow", "table")
    anchor = len(history)
    for obj, target in operations:
        if target == "hand":
            action("pick", obj)
        else:
            move(obj, target)
    for offset in unknown:
        pos = anchor+offset-1
        history[pos] = replace(history[pos], evidence="unknown")
    cursor = len(history)
    task = Contract(7, tuple(Obligation(c, anchor if scope == "past" else cursor)
                             for scope, c in (*obligations, EMPTY))).validate()
    snapshot = Snapshot(task, state, tuple(history), "obs-"+sha256(
        f"{split}/{index}/current".encode()).hexdigest()[:16]).validate()
    return {"id": f"{split}-{index:02}", "split": split, "family": family,
        "snapshot": snapshot, "instruction": "完成 ACCEPTED_TASK 的全部要求；它是已经确认的完整任务，不修改目标或时间范围。",
        "expected_status": status, "background_events": anchor,
        "annotation_source": "author_assigned_before_model_calls",
        "data_scope": "synthetic_symbolic_two_object_domain"}


def make_cases(split="development"):
    a, v, p = at, visit, protect
    backwards = [("yellow", "return"), ("yellow", "table"), ("blue", "green"), ("blue", "table")]
    forwards = [("blue", "green"), ("blue", "table"), ("yellow", "return"), ("yellow", "table")]
    if split == "development":
        specs = [
            ("normal", [], [a("blue", "green")], "ready", ()),
            ("blocker", [("yellow", "green"), ("blue", "hand")], [a("blue", "green"), a("yellow", "return")], "ready", ()),
            ("protected_blocker", [("yellow", "green"), ("blue", "hand")], [a("blue", "green"), p("yellow")], "conflict", ()),
            ("memory_not_applicable", [("blue", "green")], [a("blue", "green"), a("yellow", "return"), p("blue")], "ready", ()),
            ("past_order", backwards, [v("blue", "green"), v("yellow", "return"), order()], "conflict", ()),
            ("uncertain_order", backwards, [v("blue", "green"), v("yellow", "return"), order()], "need_observation", (2,)),
            ("restart", backwards, [v("blue", "green", scope="now"), v("yellow", "return", scope="now"), order(),
                                     a("blue", "green"), a("yellow", "return")], "ready", (2,)),
            ("already_done", [("blue", "green"), ("yellow", "return")], [a("blue", "green"), a("yellow", "return")], "ready", ()),
        ]
    elif split == "validation":
        specs = [
            ("protected_bystander", [], [a("blue", "green"), p("yellow")], "ready", ()),
            ("partial_completion", [("blue", "green"), ("yellow", "hand")], [a("blue", "green"), a("yellow", "return"), p("blue")], "ready", ()),
            ("blocked_new_order", [("yellow", "return"), ("blue", "hand")], [a("blue", "return"), a("yellow", "green"),
                v("yellow", "green", scope="now"), v("blue", "return", scope="now"), order("visit_yellow", "visit_blue")], "ready", ()),
            ("unreachable_protected_goal", [], [a("blue", "return"), p("blue")], "conflict", ()),
            ("swap_with_waypoint", [("blue", "green"), ("yellow", "return")], [a("blue", "return"), a("yellow", "green"),
                v("blue", "table", scope="now"), v("yellow", "green", scope="now"), order()], "ready", ()),
            ("protected_swap", [("blue", "green"), ("yellow", "return")], [a("blue", "return"), a("yellow", "green"), p("blue")], "conflict", ()),
            ("uncertain_but_robust_suffix", forwards, [v("blue", "green"), v("yellow", "return"), order(), a("yellow", "return")], "ready", (6,)),
            ("two_unknown_order", backwards, [v("blue", "green"), v("yellow", "return"), order(), a("blue", "return")], "need_observation", (2, 6)),
            ("old_order_not_active", backwards, [a("blue", "return"), a("yellow", "green")], "ready", (2,)),
            ("one_scope_restarted", forwards, [v("blue", "green", scope="now"), v("yellow", "return"), order()], "conflict", ()),
            ("held_object_protected", [("yellow", "hand")], [a("yellow", "return"), p("yellow")], "conflict", ()),
            ("uncertain_visit_repeatable", [("blue", "green"), ("blue", "table")], [v("blue", "green"), a("blue", "green"), p("yellow")], "ready", (2,)),
            ("completed_with_unknown_past", [("yellow", "return"), ("yellow", "table"), ("blue", "return"), ("yellow", "green")],
                [a("blue", "return"), a("yellow", "green")], "ready", (2,)),
            ("held_table_delivery", [("blue", "hand")], [a("blue", "table"), p("yellow")], "ready", ()),
            ("blocked_waypoint", [("yellow", "return"), ("blue", "hand")], [a("blue", "green"), a("yellow", "table"),
                v("blue", "return", scope="now", id="waypoint"), v("blue", "green", scope="now", id="finish"), order("waypoint", "finish")], "ready", ()),
            ("protected_waypoint_blocker", [("yellow", "return"), ("blue", "hand")], [a("blue", "green"), p("yellow"),
                v("blue", "return", scope="now", id="waypoint"), v("blue", "green", scope="now", id="finish"), order("waypoint", "finish")], "conflict", ()),
            ("completed_protected_goal", [("blue", "green")], [a("blue", "green"), a("yellow", "table"), p("blue")], "ready", ()),
            ("movable_blocker_no_extra_goal", [("blue", "return"), ("yellow", "hand")], [a("yellow", "return"),
                v("blue", "green", scope="now"), v("yellow", "return", scope="now"), order()], "ready", ()),
            ("unknown_first_visit", [("blue", "green"), ("blue", "table"), ("yellow", "return"), ("yellow", "table"),
                ("blue", "green"), ("blue", "table")], [v("blue", "green"), v("yellow", "return"), order(), a("blue", "green")], "need_observation", (2,)),
            ("capacity_conflict", [], [a("blue", "green"), a("yellow", "green")], "conflict", ()),
        ]
    else:
        raise ValueError("unknown split")
    return [make_case(i, split, family, operations, obligations, status, unknown=unknown,
                      background=3+i%4) for i, (family, operations, obligations, status, unknown) in enumerate(specs)]


def load_cases(path):
    """Use previously frozen inputs verbatim, without regenerating task labels."""
    from pathlib import Path
    result = []
    for raw in json.loads(Path(path).read_text()):
        s = raw["snapshot"]
        c = s["contract"]
        contract = Contract(c["version"], tuple(Obligation(Clause.model_validate(o["clause"]), o["since"]) for o in c["obligations"]))
        snapshot = Snapshot(contract, State(**s["state"]), tuple(Event(**e) for e in s["history"]), s["observation_id"]).validate()
        result.append({**raw, "snapshot": snapshot})
    return result
