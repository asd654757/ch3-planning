"""Author-built development and held-out wordings; labels never enter prompts.

Held-out cases reuse twelve mechanism families, so they are not independent
evidence of transfer to new task domains. No labels are computed from the solver.
"""
from dataclasses import asdict

from ch3.revision.history import Clause, Contract, Event, Obligation, Snapshot, State


def make_cases(split="dev"):
    if split not in ("dev", "heldout"):
        raise ValueError("unknown split")
    cases = []
    variants = (0,) if split == "dev" else (1, 2)
    for variant in variants:
        a, b = ("blue", "yellow") if variant != 1 else ("yellow", "blue")
        an, bn = ("蓝", "黄") if a == "blue" else ("黄", "蓝")
        x, y = ("green", "return") if variant != 2 else ("return", "green")
        xn, yn = ("绿色区", "返回区") if x == "green" else ("返回区", "绿色区")
        c = lambda id, kind, **kw: Clause(id=id, kind=kind, **kw)
        ga = c("ga", "at", object=a, target=x)
        gb = c("gb", "at", object=b, target="table")
        empty = c("empty", "empty")
        va = c("va", "visit", object=a, target=x)
        vb = c("vb", "visit", object=b, target=y)
        order = c("order", "before", first="va", second="vb")
        f = c("protect", "forbid", object=b)
        def state(aloc="table", bloc="table", holding=None):
            return State(**{a: aloc, b: bloc, "holding": holding})
        def history(first=a, unknown=False):
            other = b if first == a else a
            dest = {a: x, b: y}
            events = []
            for obj in (first, other):
                for kind, target in (("pick", None), ("place", dest[obj]),
                                     ("pick", None), ("place", "table")):
                    events.append(Event(len(events)+1, kind, obj, target,
                        "unknown" if unknown and len(events) == 1 else "confirmed"))
            return tuple(events)
        def add(family, initial, update, revised, status="ready", events=(), current=None, alternative=None):
            snapshot = Snapshot(Contract(0, tuple(Obligation(z) for z in initial)),
                                current or state(), tuple(events), f"{split}-{family}-{variant}").validate()
            # Explicit gold anchors: preserved clauses keep their activation;
            # new/changed clauses activate at the revision boundary.
            original = {z.id: z for z in initial}
            gold = Contract(1, tuple(Obligation(z, 0 if original.get(z.id) == z else snapshot.cursor)
                                     for z in revised)).validate()
            cases.append({"id": f"{split}-{family}-{variant}", "family": family, "split": split,
                          "initial_instruction": "已接受的任务由 INITIAL_TASK 完整给出，条款均从记录开始时生效。",
                          "update": update if variant == 0 else (alternative or update),
                          "snapshot": snapshot, "gold": gold, "gold_status": status})
        add("retarget", [ga, gb, empty, f], f"把{an}色块的最终目的地改为{yn}，其他照旧。",
            [c("ga", "at", object=a, target=y), gb, empty, f],
            alternative=f"别再把{an}块留在{xn}了，最终应在{yn}；{bn}块和空手等已有约束照旧。")
        add("cancel_dependency", [va, vb, order, empty], f"取消{an}色块经过{xn}的要求，并取消相关先后顺序；{bn}色块的必经点和空手要求保留。",
            [vb, empty], alternative=f"只删掉{an}块的必经要求及两块之间的顺序约束。{bn}块照常经过{yn}，收尾仍空手。")
        add("past_order", [va, vb, empty], f"补充：原记录中的这两个首次放置必须先{an}后{bn}。按原记录判断，不许通过重做替代。",
            [va, vb, order, empty], "conflict", history(b),
            alternative=f"用执行记录核对原有必经要求：首次{an}块进{xn}须早于首次{bn}块进{yn}。不重新起算。")
        add("uncertain_order", [va, vb, empty], f"原有两次必经放置必须先{an}后{bn}。首次事件按记录判，未确认不能当作确认，也不许重置起点。",
            [va, vb, order, empty], "need_observation", history(b, True),
            alternative=f"在原有必经条款间加先{an}后{bn}约束，保留它们原来的起算点；记录中的未知事件需要核实。")
        va2 = c("va_again", "visit", object=a, target=x)
        vb2 = c("vb_again", "visit", object=b, target=y)
        new_order = c("order", "before", first="va_again", second="vb_again")
        fa = c("ga", "at", object=a, target="table")
        add("restart", [va, vb, fa, gb, empty], f"从现在重新做必经放置：先把{an}块放入{xn}，再把{bn}块放入{yn}。以前做过的不算本轮，两项旧必经要求替换为新一轮；最终都回桌面且空手。",
            [va2, vb2, new_order, fa, gb, empty], events=history(b),
            alternative=f"重开一轮，不使用旧放置抵扣：{an}块先到{xn}，{bn}块随后到{yn}。替换两项旧必经条款；两块最终回桌面、手空的要求不变。")
        forbidden_a = c("new_protect", "forbid", object=a)
        add("held_prohibition", [ga, empty], f"从现在起禁止再操作{an}块。其他任务要求不变。",
            [ga, empty, forbidden_a], "conflict", (Event(1, "pick", a, None),), state("hand", holding=a),
            alternative=f"追加不可操作{an}块的限制，立即生效；此前的目的地和空手要求继续保留。")
        add("capacity", [ga, c("gb", "at", object=b, target=y), empty], f"{bn}块最终也放到{xn}。{an}块的目的地和空手要求不变。",
            [ga, c("gb", "at", object=b, target=x), empty], "conflict",
            alternative=f"只把{bn}块的最终位置由{yn}改成{xn}，其余最终要求全部保留。")
        add("lift_protection", [ga, gb, f, empty], f"取消不能操作{bn}块的限制，允许临时挪动它；所有最终位置和空手要求不变。",
            [ga, gb, empty], current=state("hand", x, a),
            alternative=f"为腾出位置，现在允许搬动{bn}块，撤掉对应禁令。两块的终点及空手条件照旧。")
        add("protect_completed", [ga, c("gb", "at", object=b, target=y), empty], f"{an}块已经放好，从现在起不许再操作它；其他要求保留。",
            [ga, c("gb", "at", object=b, target=y), empty, forbidden_a],
            events=(Event(1, "pick", a, None), Event(2, "place", a, x)), current=state(x),
            alternative=f"追加保护{an}块的要求：从此别再碰它。现有最终目标及空手条件都继续有效。")
        old_forbid = c("protect", "forbid", object=a)
        add("cancel_past_prohibition", [old_forbid, empty], f"撤销不能操作{an}块的条款；执行记录保留，最后仍须空手。",
            [empty], events=history(a),
            alternative=f"把关于{an}块的操作禁令从任务中删除，不改动执行记录，也不取消空手要求。")
        add("changed_waypoint_order", [va, vb, order, empty], f"把{an}块的必经点从{xn}改为{yn}。这项新必经要求从现在生效，但原有先{an}后{bn}及其他要求不变，{bn}的必经条款不重置。",
            [c("va", "visit", object=a, target=y), vb, order, empty], "conflict", history(a),
            alternative=f"仅修订{an}块的必经位置为{yn}并从现在计算；原先的先后顺序继续约束两项必经条款，{bn}块仍沿用旧记录。")
        add("repeat_one", [va, empty], f"此前的{an}块放置不计这一次。将旧必经条款替换为从现在再次把{an}块放入{xn}，最后空手。",
            [va2, empty], events=history(a),
            alternative=f"重新要求{an}块经过{xn}一次：旧事件不抵扣，替换旧必经条款并从当前时刻起算。仍须空手结束。")
    return cases


def serialize_case(case):
    return {k: asdict(v) if k in ("snapshot", "gold") else v for k, v in case.items()}


def json_default(value):
    if isinstance(value, Clause):
        return value.model_dump(exclude_none=True)
    raise TypeError(type(value).__name__)
