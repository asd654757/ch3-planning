"""Read-only, failure-inclusive analysis of a complete budgeted recovery pilot."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path


def analyze(root: Path):
    manifest=json.loads((root/"manifest.json").read_text())
    rows=json.loads((root/"pilot_results.json").read_text())
    frozen=json.loads((root/"frozen_model.json").read_text())
    expected={(seed,case,b,method) for seed in manifest["pilot_seeds"]
              for case in manifest["cases"] for b in range(len(frozen["budgets"])) for method in manifest["methods"]}
    index={}
    violations=[]
    for row in rows:
        key=(row["seed"],row["case"],row["budget_index"],row["method"])
        if key in index:
            raise ValueError("duplicate pilot assignment")
        index[key]=row
        if row.get("episode_resets") != 1 or row["api_calls"] != 0:
            violations.append({"key":key,"reason":"reset_or_api_boundary"})
        remaining=dict(row["initial_budget"])
        for event in row["decisions"]:
            if event["action"] not in event["allowed"]:
                violations.append({"key":key,"reason":"dispatch_outside_common_permissions"})
            if event["budget_before"] != remaining:
                violations.append({"key":key,"reason":"budget_ledger_mismatch"})
            if "actual_cost" in event:
                for resource,spent in event["actual_cost"].items():
                    remaining[resource]-=spent
                if min(remaining.values())<0:
                    violations.append({"key":key,"reason":"negative_budget"})
    if set(index)!=expected:
        raise ValueError("missing or unexpected pilot assignments")
    source_checks={str(p): hashlib.sha256((root/"sources"/Path(p).name).read_bytes()).hexdigest()==h
                   for p,h in manifest["source_hashes"].items()}
    def totals(subset):
        return {"successes":sum(bool(r["task_success"]) for r in subset),"assigned":len(subset),
            "noncomplete_runs":sum(r["status"]!="complete" for r in subset),
            "recovery_control_steps":sum(r.get("recovery_control_steps",0) for r in subset),
            "stops":sum(any(d["action"]=="stop" for d in r["decisions"]) for r in subset)}
    aggregate={m:totals([r for r in rows if r["method"]==m]) for m in manifest["methods"]}
    by_budget={str(b):{m:totals([r for r in rows if r["budget_index"]==b and r["method"]==m])
                       for m in manifest["methods"]} for b in range(len(frozen["budgets"]))}
    by_case={c:{m:totals([r for r in rows if r["case"]==c and r["method"]==m])
                 for m in manifest["methods"]} for c in manifest["cases"]}
    paired={}
    for comparator in ("rule","depth1"):
        counts=defaultdict(int)
        steps=[]
        for seed,case,b,method in sorted(expected):
            if method!="depth2":
                continue
            full=index[(seed,case,b,"depth2")]
            base=index[(seed,case,b,comparator)]
            counts[f"depth2_{int(full['task_success'])}_base_{int(base['task_success'])}"]+=1
            steps.append(full["recovery_control_steps"]-base["recovery_control_steps"])
        paired[comparator]={"success_2x2":dict(counts),"control_step_deltas":steps,
                            "sum_control_step_delta":sum(steps)}
    return {"scope":manifest["scope"],"assigned":len(rows),"aggregate":aggregate,
        "by_budget":by_budget,"by_case":by_case,"paired":paired,"budgets":frozen["budgets"],
        "rule_threshold":frozen["rule_threshold"],"violations":violations,"source_snapshot_checks":source_checks,
        "ready_for_claim":False,
        "limitations":["Two pilot layouts, repeated conditions/budgets are correlated, not independent tasks.",
                       "No claim of superiority, equivalence, calibrated uncertainty or open-world capability.",
                       "No direct LLM replanning or tuned decision-tree comparison."]}


def markdown(result, root):
    rows=["# 预算恢复实际pilot结果", "", f"原始记录：{root.resolve()}", "",
          "模型仅来自独立18次开发执行；pilot条件、预算及方法在各版本开始前固定。",
          "这是开发诊断，不是正式优效评估。没有LLM调用。", "",
          "| 方法 | 独立终态完成/分配运行 | 恢复控制步数合计 | 异常运行 |",
          "|---|---:|---:|---:|"]
    for method,total in result["aggregate"].items():
        rows.append(f"| {method} | {total['successes']}/{total['assigned']} | {total['recovery_control_steps']} | {total['noncomplete_runs']} |")
    rows += ["", "## 分预算结果", "", "| 恢复步数预算 | rule | depth1 | depth2 |", "|---:|---:|---:|---:|"]
    for b,methods in result["by_budget"].items():
        counts=[f"{methods[m]['successes']}/{methods[m]['assigned']}" for m in ("rule","depth1","depth2")]
        rows.append(f"| {result['budgets'][int(b)]['time_units']} | " + " | ".join(counts)+" |")
    rows += ["", "## 分条件结果（跨三预算，不能当独立任务数）", "",
             "| 条件 | rule | depth1 | depth2 |", "|---|---:|---:|---:|"]
    for case,methods in result["by_case"].items():
        counts=[f"{methods[m]['successes']}/{methods[m]['assigned']}" for m in ("rule","depth1","depth2")]
        rows.append(f"| {case} | " + " | ".join(counts)+" |")
    rows += ["", "## 判读", "", f"开发集选定固定观察阈值为{result['rule_threshold']}，不是在pilot结果上选择。",
             "主指标为预算内独立终态完成；停止仍是未完成，不能因省观察而称为任务收益。",
             "深度1和深度2使用相同概率模型、权限映射、成本与边界值。",
             "若两者持平，不能将共同预算门控带来的节省归因于多步前瞻。", "",
             "本表是预设等权条件的描述性汇总。只有两个layout seed，不做显著性或等效宣称。",
             "外部缺失按采集序号施加，不是自然时序故障。宏动作cap由开发最大步数×1.25确定，",
             "其预测误差和保守性也属于方法限制。接触后释放仍无重试授权，全部保留未完成结果。", "",
             f"权限/资源/初始化审计违规数：{len(result['violations'])}。源码快照哈希全部通过：{all(result['source_snapshot_checks'].values())}。",
             "源码/字段检查是有限审计，不是完整信息流或安全证明。", ""]
    return "\n".join(rows)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    result=analyze(args.input)
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/"analysis.json").write_text(json.dumps(result,indent=2))
    (args.output/"report.md").write_text(markdown(result,args.input),encoding="utf-8")
    print(json.dumps(result["aggregate"]))


if __name__=="__main__":
    main()
