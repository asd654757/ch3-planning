# 方法设计修正：执行阶段分离与状态感知修复（2026-09-12）

## 1. 关键语义澄清：合法前缀是否已经执行？

R1 与 R2 不再作为互相竞争的同类修复方法，而是分属两个执行阶段：

```
执行前（plan-time）：
  VLM计划 → Validator → 非法 → R1 完整重规划（计划尚未执行，
  已验证前缀不可修改的前提不存在）

执行中（run-time）：
  已执行前缀 → 真实状态反馈 → R2 仅生成剩余后缀
  （前缀已在机器人上执行，不可回退，因此必须锁定）
```

论文表述：
- R1 解决**执行前纠错**：重新生成完整计划，修复空间最大；
- R2 解决**执行中恢复**：前缀已执行、不可撤销，只能安全补全后缀；
- formal_v8 的压力实验将 R2 的前缀视为"已执行语义"（validator 的
  `final_state` 即前缀执行后的状态），后续采集已在 prompt 中显式给出
  `prefix_final_state`。

## 2. R2 状态感知输入（已实现，2026-09-12）

R2 prompt_input 新增（全部程序计算，模型不得自行推断）：

| 字段 | 来源 |
|---|---|
| `prefix_final_state` | `validation.final_state` 展开 facts + 空手事实 |
| `held_objects` | `final_state.holding`（arm → object_id） |
| `remaining_goal_facts` | goal facts 与前缀终态的差集 |
| `next_step_id` | `len(validated_prefix) + 1` |

output_requirement 同时明确：**锁定前缀末尾的 pick 可由后缀第一个
place 完成配对**（跨前缀 pick/place 允许），且持物手臂必须先 place
才能 pick 新物体。

注意：该 prompt 改动改变 prompt hash，新 R2 数据不得与 formal_v8 旧
R2 混合比较。

## 3. 自适应修复路由（2026-09-12 核心路径已实现）

```
VLM Planner
→ ModelPlan
→ 四层 Validator
→ 修复路由器
   ├─ Deterministic Truncation（合法计划后仅冗余非法尾部，程序截断）
   ├─ Full Replanning / R1（执行前纠错）
   └─ State-aware Suffix Repair / R2（执行中恢复，持物状态感知）
→ R2 失败回退 R1_FROM_STATE（从 prefix_final_state 只规划剩余后缀）
→ Goal Checker
→ ExecutablePlan
→ AD-Flow
```

当前实现状态：`ROUTED` 核心路径已落地于
`scripts/repair_pressure.py`，并已覆盖单元测试。
其路由规则是先执行状态感知 R2；若 R2 结果未通过 Validator 或
Goal Checker，则自动回退 `R1_FROM_STATE`。该 fallback 不返回/不执行
完整旧计划，而是以 `prefix_final_state` 为当前状态重新规划剩余任务，
再程序化合并到已执行前缀之后。记录中新增 `repair_mode="ROUTED"`、
`route_taken`、`fallback_triggered`、`r2_valid`、`r2_goal_ok` 与
`r2_pbw` 字段。

注意：程序侧确定性截断（Deterministic Truncation）仍属后续增强，
尚未实现为独立路由分支；本轮 pilot 不声称该项已完成。

## 4. R2 Pilot 预注册方案与决策标准

### 数据源
- 冻结 baseline 复用：`--source-collection data/collections/repair_pressure_formal_v8_no_oracle_20260911_153655.jsonl`
  （`pressure_source_plan` 提供 48 个 task×seed 的原始 baseline，
  0 次 baseline 调用）。
- formal_v8 实际为 16 任务 × 3 seeds，因此 pilot 为
  **6 任务 × 3 seeds × 2 压力 = 36 次 R2 调用**（非 60；5 seeds 会
  破坏冻结 baseline 复用）。

### 任务选择（预注册规则）
取目标压力上 formal_v8 R2 最差的 6 个任务（goal-rate 升序，
同率按 task_id 升序）：
`attribute_grouped_002, attribute_grouped_003, attribute_grouped_007,
exclusion_constraint_010, attribute_grouped_000, attribute_grouped_004`

### 压力类型
仅 `unknown_object_after_prefix` 与 `duplicate_pick_after_prefix`
（各 18 例）。

### 决策标准（预先固定，不看总体平均）
- `unknown_object_after_prefix`：≥ 60%（formal 基线 2.1%）；
- `duplicate_pick_after_prefix`：≥ 80%（formal 基线 58.3%）；
- VGF（合法但未达标）：0；
- prefix mutation（已验证前缀被修改）：0；
- pass-but-wrong：≤ 2%；
- 失败样本中 `ARM_NOT_EMPTY` 占比相对 formal 明显下降。

### 分支
- 达标 → 重跑 R2 正式（36 slices × 5 pressures = 180 次调用，
  复用冻结 baseline）；
- 未达标 → 停止继续调 prompt，采用
  "执行前 R1 + 执行中状态感知 R2 + R2 失败回退"的路由框架，
  以负结果/权衡分析写进论文。

## 5. Pilot 结果（2026-09-12，两轮）

### 第 1 轮（已作废：发现 pipeline 缺陷）
`repair_pressure_r2_state_aware_pilot_20260912_132529.jsonl`。
发现：`unknown_object_after_prefix` 在对象层失败时
`validation.final_state = None`，R2 的全部状态字段未进入 prompt
（模型被要求使用不存在的字段）。该轮测的是残缺输入，结论作废。

**Pipeline 修复**：`Validator._prefix_state()` —— 只要有合法前缀，
即使失败发生在 object/capability 层，也模拟前缀生成
`prefix_final_state`（前缀具有"已执行"语义）。回归测试
`test_object_layer_failure_still_reports_prefix_final_state`。

### 第 2 轮（有效 pilot）
`repair_pressure_r2_state_aware_pilot2_20260912_133131.jsonl`。
6 任务 × 3 seeds × 2 压力 = 36 次 R2 调用，冻结 baseline 全部复用，
0 次 baseline 调用，耗时 113 s。

| 压力 | n | valid | goal | VGF | invalid codes |
|---|---:|---:|---:|---:|---|
| unknown_object_after_prefix | 18 | 67% | **39%** | 5 | ARM_NOT_EMPTY ×6 |
| duplicate_pick_after_prefix | 18 | 100% | **100%** | 0 | — |

prefix mutation：0。

### 对照预注册决策标准

| 标准 | 要求 | 实际 | 结论 |
|---|---|---|---|
| unknown goal-rate | ≥60% | 39%（valid 67%） | ❌ |
| duplicate goal-rate | ≥80% | 100% | ✅ |
| VGF | 0 | 5/36（14%） | ❌ |
| prefix mutation | 0 | 0 | ✅ |
| pass-but-wrong | ≤2% | 14% | ❌ |

**决策：未达标，按预注册分支停止调 prompt。**

### 诊断与解释

- `duplicate_pick_after_prefix`（状态层压力）：状态感知 R2 完全修复
  58.3%→100%。R2 的"执行中恢复"语义成立。
- `unknown_object_after_prefix`（对象层压力）：39%（formal 2.1%），
  有实质改善但仍不达标。失败模式有两类：
  1. 模型跟随损坏计划的后缀形态，输出单个 pick（忽略
     `held_objects`），ARM_NOT_EMPTY ×6；
  2. 5 例 VGF：模型正确处理了前缀边界，但后缀只完成
     `remaining_goal_facts` 的一部分即截断。
- 解释：未知对象注入意味着"场景模型错误"，这在真实系统中应由
  重感知/重规划处理，而不是后缀补全；R2 的语义边界就在这里。

### 论文采用结论

按预注册分支采用三层框架，不再单独调 R2 prompt：

```
执行前纠错：R1 完整重规划
执行中恢复：状态感知 R2（状态层错误，duplicate/ARM_NOT_EMPTY 类）
失败回退：  R2 未通过校验 → 回退 R1（从当前状态规划剩余任务）
```

实验表述：状态感知使 R2 在其语义范围（状态恢复）内从 58.3%→100%，
在超出语义范围（对象重写）时不足，需要回退路由。这正好支撑
"按执行阶段与错误类型路由修复策略"的方法主张。

## 6. ROUTED pilot 与 VGF 根因（2026-09-12）

第二轮 R2-only pilot 的 5 例 VGF 逐条检查后确认：均为
`merged_with_prefix=True` 且模型返回空后缀。模型正确处理了“删除非法
尾部”这一合法操作，但当前压力场景中的合法前缀本身尚未完成全部任务；
空后缀 merge 后计划合法但不完整。这直接支持两个方法结论：

1. 结构合法性不等于任务完成性，必须同时使用 Validator 和 Goal Checker；
2. R2-only 不应作为统一修复器，需要系统级路由与回退。

随后实现 `ROUTED` 核心路径：状态感知 R2 先行，Validator/Goal Checker
失败后自动回退 R1。Routed pilot 为 6 任务 × 3 seeds × 2 压力 = 36 例，
冻结 baseline 全部复用（0 次 baseline 调用），输出：

`data/collections/repair_pressure_routed_pilot_20260912_134946.jsonl`

| 压力 | n | CRR | Final Goal | VGF | R2 直接成功 | R1 回退成功 |
|---|---:|---:|---:|---:|---:|---:|
| duplicate_pick_after_prefix | 18 | 100% | 100% | 0 | 15 | 3 |
| unknown_object_after_prefix | 18 | 100% | 100% | 0 | 8 | 10 |
| overall | 36 | 100% | 100% | 0 | 23 | 13 |

论文措辞：

- 不写“R2 在 unknown-object 上达到 100%”；
- 写“R2 在状态恢复语义范围内直接修复多数状态层错误；对象引用重写
  超出固定后缀补全边界，由 R1 回退承担”；
- 写“ROUTED 在该 pilot 中将系统级任务完成率恢复到 100%，VGF 为 0”；
- 该 pilot 是机制验证，不替代 formal v9 大样本正式实验。

数据血缘与旧版本定位见 `docs/data_lineage_20260912.md`。

## 7. 执行边界安全修正（2026-09-12）

初审发现：先前的 `ROUTED` fallback 使用普通 R1，prompt 的
`current_state` 仍为初始状态，且返回完整计划。这在执行中修复语义下
可能重复执行已确认前缀。

已实现新的 fallback 模式 `R1_FROM_STATE`：

```
R2 失败
→ 只保留 validated_prefix / prefix_final_state / held_objects /
  remaining_goal_facts / next_step_id
→ R1_FROM_STATE 仅返回 remaining-task suffix
→ 程序执行 merge_locked_prefix(executed_prefix, returned_suffix)
→ Validator + Goal Checker
```

约束：

- `R1_FROM_STATE` 的 `current_state` 必须是 `prefix_final_state`；
- prompt 不含损坏 `original_plan`，只含 `executed_prefix`；
- 返回后缀第一步步号必须为 `len(prefix)+1`；
- 前缀动作不允许出现在返回后缀中；
- 程序侧合并后再次校验完整计划。

因此，`repair_pressure_routed_pilot_20260912_134946.jsonl` 降级为
**fallback 语义修正前的 router 机制诊断数据**，不作为正式安全修复
结果。后续必须使用 `R1_FROM_STATE` 重新采集 routed pilot / formal。

## 8. R1_FROM_STATE ROUTED pilot（2026-09-12）

在执行边界修正后重新采集 36 例受控 pilot：

`data/collections/repair_pressure_routed_state_pilot_20260912_141702.jsonl`

| 指标 | 结果 |
|---|---:|
| pressure points | 36 |
| baseline slices | 18/18 valid |
| CRR | 36/36 = 100% |
| Final Goal / GSR after repair | 36/36 = 100% |
| VGF | 0/36 = 0% |
| pass-but-wrong | 0/36 |
| R2 direct | 25/36 |
| R1_FROM_STATE fallback | 11/36 |
| total repair calls | 47 |
| average calls per pressure | 47 / 36 = 1.306 |

按压力：

| 压力 | n | Final Goal | R2 direct | R1_FROM_STATE fallback |
|---|---:|---:|---:|---:|
| duplicate_pick_after_prefix | 18 | 18/18 | 17 | 1 |
| unknown_object_after_prefix | 18 | 18/18 | 8 | 10 |

Fallback 审计：

- 11 个 fallback 全部满足最终目标；
- 原始模型后缀没有包含已执行前缀 step；
- 全部 fallback prompt 含 `holding(...)` / `prefix_final_state`；
- 没有空后缀导致的合法但不完整样本。

结论措辞：

> 在修正执行边界后的受控 pilot 中，R2 优先、R1_FROM_STATE 回退的
> ROUTED 机制保持 100% 系统级任务完成，平均每压力点 1.31 次修复调用。
> 该结果仍是机制验证，不替代 formal v9。

## 9. Formal v9 ROUTED 正式结果（2026-09-12）

正式采集文件：

`data/collections/repair_pressure_formal_v9_routed_20260912_142533.jsonl`

协议：

- 16 任务 × 3 seeds × 5 压力类型 = **240 个压力点**；
- 48/48 个 task×seed baseline 全部有效；
- 复用 formal_v8 no-oracle 的冻结 baseline，0 次 baseline 调用；
- 模型：`qwen3-vl-flash`；
- 路由协议：状态感知 R2 先行，失败后按有无合法前缀终态回退
  `R1_FROM_STATE` 或普通 `R1`；
- 耗时：884.0 s；
- 机器可读汇总：
  `data/reports/formal_v9_routed_metrics_20260912.json`。

### 9.1 总体结果

| 指标 | 结果 |
|---|---:|
| pressure points | 240 |
| CRR / valid after repair | 223/240 = **92.9%** |
| Final Goal / GSR after repair | 215/240 = **89.6%** |
| VGF（合法但未达成目标） | 8/240 = **3.3%** |
| pass-but-wrong | 8/240 = 3.3% |
| accepted | 238/240 |
| R2-only 路由 | 140/240 = 58.3% |
| fallback 路由 | 100/240 = 41.7% |
| 修复模型调用 | 340 次 |
| 平均调用次数 | 340/240 = **1.42 次/压力点** |

路由明细：

| 路由 | 数量 |
|---|---:|
| R2 直接成功 | 140/240 |
| `R2 → R1_FROM_STATE` | 70/240 |
| `R2 → R1` | 30/240 |
| fallback 后最终成功 | 75/100 |

因此，论文不能把 pilot 的 100% 扩展为普遍结论。formal v9 的正确主张
是：**ROUTED 将系统级任务完成率恢复到 89.6%，明显高于外部基线；
剩余失败集中在可诊断的压力类型上。**

### 9.2 分压力结果

| 压力类型 | n | CRR | Final Goal | VGF | R2 直接成功 | fallback |
|---|---:|---:|---:|---:|---:|---|
| duplicate_pick_after_prefix | 48 | 100% | 100% | 0 | 41/48 | 7/7 成功 |
| invalid_target_after_prefix | 48 | 100% | 89.6% | 5 | 25/48 | 18/23 成功 |
| place_before_pick | 48 | 100% | 100% | 0 | 18/48 | 30/30 成功 |
| repeat_pick_after_valid_plan | 48 | **64.6%** | **64.6%** | 0 | 31/48 | 0/17 成功 |
| unknown_object_after_prefix | 48 | 100% | 93.8% | 3 | 25/48 | 20/23 成功 |

解释：

- `duplicate_pick_after_prefix` 仍然最贴合 R2 的执行中状态恢复语义，
  状态感知 R2 直接成功率从 legacy 的 58.3% 提升到 85.4%，ROUTED 最终
  达到 100%。
- `place_before_pick` 没有合法前缀终态，router 自动回退普通 R1；
  30/30 全部恢复。
- `unknown_object_after_prefix` 大部分由 R1_FROM_STATE 回退救回，
  说明固定后缀补全不适合对象引用重写，但系统级回退有效。
- `invalid_target_after_prefix` 与 `unknown_object_after_prefix` 共有
  8 例 VGF。它们通过 Validator 但未通过 Goal Checker，再次说明结构
  合法性不等于任务完成性。

### 9.3 唯一主要失败簇与根因

formal v9 的 17 个 invalid 结果全部来自
`repeat_pick_after_valid_plan`：

| 任务 | 失败 seeds |
|---|---|
| attribute_grouped_003 | 0, 2 |
| exclusion_constraint_009 | 0, 1, 2 |
| exclusion_constraint_012 | 0, 1, 2 |
| exclusion_constraint_013 | 0, 1, 2 |
| exclusion_constraint_014 | 0, 1, 2 |
| exclusion_constraint_015 | 0, 1, 2 |

失败语义不是“无法恢复任务”，而是：**压力计划在一个已经完成目标的
合法计划后追加了重复 pick，program 侧缺少确定性尾部截断分支**。
其中：

- 9 例最终解析为 `E01 plan_parse_error`；
- 5 例是模型尝试从已完成状态继续 pick，触发
  `STATE_TRANSITION_ERROR`；
- 3 例因误解“只返回后缀”的约束，输出不完整动作序列，触发
  `SCHEMA_ERROR`；
- 2 例模型返回 infeasible refusal 对象，但 `remaining_goal_facts`
  实际为空，goal 已在已执行前缀终态满足。

同时，`R1_FROM_STATE` prompt 的 hard constraint 8 仍写着
“For R0/R1, return the complete plan”，与 R1_FROM_STATE 的
“only return remaining-task suffix”存在措辞冲突。该措辞不是安全问题，
但会放大模型在“空后缀是否合法”上的犹豫。

### 9.4 与 legacy / external baseline 的对比

| 方法 | n | CRR | Final Goal | 说明 |
|---|---:|---:|---:|---|
| legacy R0 | 240 | 41.2% | 41.2% | 从头重试，legacy 协议 |
| legacy R1 | 240 | 100% | 99.2% | 执行前完整重规划，不宜与 R2/ROUTED 当作同一阶段直接竞争 |
| legacy R2 | 240 | 55.8% | 55.8% | 无状态感知输入的旧式 R2 |
| Self-Refine | 240 | 57.1% | 55.8% | 协议级外部基线 |
| Checker-loop | 240 | 79.6% | 79.6% | 协议级外部基线 |
| **ROUTED formal v9** | 240 | **92.9%** | **89.6%** | R2 优先 + R1_FROM_STATE/R1 回退 |

对比措辞：

> 在 240 个受控修复压力点上，ROUTED 的最终任务完成率为 89.6%，高于
> 协议级 Checker-loop 的 79.6% 和 Self-Refine 的 55.8%。ROUTED 的优势
> 不是 R2 单独优于所有方法，而是把状态恢复、对象引用重写和无前缀
> 纠错路由到不同修复边界。

### 9.5 论文采用边界

formal v9 可以作为 ROUTED 的正式主结果；pilot 数据仍只作机制验证。
但结果章节必须同时报告：

1. 92.9% CRR、89.6% GSR 和 3.3% VGF；
2. 平均 1.42 次修复调用的成本；
3. `repeat_pick_after_valid_plan` 的失败簇与缺失确定性截断；
4. Validator 与 Goal Checker 的分工；
5. `place_before_pick` 回退普通 R1、`unknown_object`/`invalid_target`
   回退 R1_FROM_STATE 的路由差异。

若后续做 v10，预注册增强应是：

```
if prefix_final_state already satisfies all goal facts
   and no arm is holding an object:
       return / merge empty suffix (deterministic tail truncation)
else:
       R2 → R1_FROM_STATE / R1 fallback
```

同时把 R1_FROM_STATE hard constraint 8 改成：

> For R1_FROM_STATE, return only the remaining-task suffix; if
> `remaining_goal_facts` is empty, return exactly `{"actions": []}`.

v10 必须使用新时间戳采集，不得覆盖 formal v9。

## 10. v10 修复：确定性尾部截断与 R1_FROM_STATE 空后缀（已实现）

formal v9 暴露的唯一主要失败簇是“完整目标已由合法前缀完成，但压力
计划追加了冗余非法尾部”。针对该失败簇，v10 加入程序侧确定性截断：

```
if validation.validated_prefix 非空
   and validation.final_state 非空
   and prefix_final_state 已满足全部 goal facts
   and final_state.holding 为空:
       ROUTED = Deterministic Truncation（不加模型调用）
else:
       R2 → R1_FROM_STATE / R1 fallback
```

安全边界：

- 只在已有合法/已执行前缀时触发；
- 只在剩余目标事实为空时触发；
- 只在双臂均不持物时触发；
- 不使用相似对象替换；
- 不改变已执行前缀；
- 结果仍会重新通过 Validator 和 Goal Checker。

记录字段新增：

```json
{
  "route_taken": ["DETERMINISTIC_TRUNCATION"],
  "r2_skipped": true,
  "r2_valid": null,
  "r2_goal_ok": null,
  "r2_pbw": null,
  "deterministic_truncation": true
}
```

同时，`repair.md` 的 hard constraint 8 修正为：

> For R2 and R1_FROM_STATE, return only the requested suffix. For
> R1_FROM_STATE, if `remaining_goal_facts` is empty, return exactly
> `{"actions": []}`.

该修改会改变 prompt hash；后续采集必须使用新时间戳并标记 formal v10，
不得与 formal v9 混合。当前测试：`79 passed`。

### 10.1 v10 压力源协议修正

formal v9 使用旧协议时，`baseline_valid` 的含义只是
`Validator.valid`；这允许了一个状态合法但未完成任务的计划进入压力源。
事后审计发现 `exclusion_constraint_012` 的 3 个 seed 都属于这种
“state-valid, goal-failing”源：baseline 只完成
`white_wedge_104 → white_bowl_106`，未完成
`red_sphere_105 → red_tray_107`。

这不是 Goal Checker 判错，而是压力 runner 的准入条件过宽。v10 修正为：

```
Pressure source accepted iff
    source_validation.valid
    and source_goal_satisfied
```

同时在 JSONL 中显式记录：

```json
{
  "pressure_source_valid": true,
  "pressure_source_goal_satisfied": true
}
```

formal v10 使用 `stress_tasks_v8_pilot_v3_source_goal.jsonl`。该版本
修正了 `exclusion_constraint_012` 的 instruction 表述，使自然语言描述
与 Goal facts 一致：red sphere 放到 red tray，而不是放进 bowl。
冻结源文件为
`frozen_sources_v10_20260912_071103.jsonl`：45 个 task/seed 继续复用
formal v8 no-oracle 的旧冻结源，`exclusion_constraint_012` 的 3 个
seed 在 v3 任务定义下重新生成，且三条都在首次生成时通过
Validator 与 Goal Checker。

v10 source-goal pilot（48 个 repeat_pick 压力点）结果为：

- Final Goal：48/48；
- VGF：0/48；
- 全部路由：`DETERMINISTIC_TRUNCATION`；
- 0 次 VLM 修复调用；
- `exclusion_constraint_012` 三个 seed 全部成功。

这只验证 v10 压力源和截断分支，不用于最终正式性能结论。

v10 formal 决策标准：

1. `repeat_pick_after_valid_plan` Final Goal ≥ 95%（v9 为 64.6%）；
2. ROUTED 总体 Final Goal ≥ 95%（v9 为 89.6%）；
3. VGF ≤ 2%（v9 为 3.3%）；
4. prefix mutation 仍为 0；
5. R2 / R1_FROM_STATE 回退语义不变；
6. `DETERMINISTIC_TRUNCATION` 不产生 VLM 调用。

## 11. v11 修复：required_transports 与状态约束后缀生成

formal v10 达成了 repeat_pick 修复和确定性截断目标，但暴露出
`R1_FROM_STATE` 的“局部补全”倾向：模型经常只完成当前机械臂手持对象的
place，而遗漏 remaining goal facts 中的其他搬运需求，导致
“结构合法但目标未完成”的 VGF 样本。为此，v11 在程序侧增加
`required_transports`。

该字段的推导规则如下：

```text
declared goal facts + prefix_final_state
    → 对每条 on(object, target) 检查 object 当前是否已在 target
    → 未完成项写入 required_transports
```

每条记录包含：

```json
{
  "object_id": "...",
  "target_id": "...",
  "currently_held": false,
  "current_location": "table"
}
```

这不是 oracle，也不是相似对象替换；它只把已经声明给系统的任务目标和
已验证前缀终态转换为显式搬运需求。`R2` 与 `R1_FROM_STATE` 的 prompt
同时加入以下约束：

1. 后缀必须完成全部 `required_transports`；
2. 当前已持有对象只需 place；
3. 未持有对象必须 pick 后立即 place；
4. 除非没有剩余搬运项，不得只完成第一个 transport 就停止。

该修改会改变 prompt hash，因此正式采集使用新时间戳并标记为
formal v11，不与 v9/v10 混合。

### 11.1 v11 pilot

针对 v10 剩余 VGF 最集中的
`exclusion_constraint_011 unknown_object_after_prefix`，先运行 3 seed
目标 pilot：

- Final Goal：3/3；
- VGF：0/3；
- 全部由 R2 直接完成，未触发回退。

随后复跑 v10 中 VGF 最集中的 18 个压力点
（`exclusion_constraint_011/014/015` × 3 seeds × duplicate/unknown）：

- Final Goal：18/18；
- VGF：0/18；
- pass-but-wrong：0/18；
- R2 直接成功：16/18；
- `R2 → R1_FROM_STATE`：2/18。

### 11.2 formal v11 结果

formal v11 使用与 v10 相同的冻结源
`frozen_sources_v10_20260912_071103.jsonl`，核心压力集仍为：

```text
48 accepted feasible task/seed slices
× 5 pressure types
= 240 repair-pressure points
```

采集日志中出现的 66 个 task/seed 包含 18 个逻辑冲突不可行任务源，
这些源被 source-goal 双检拒绝，只产生 `pressure_source` 记录，
不进入 240 个修复压力点，因此没有污染正式结果。

formal v11 总体结果：

| 指标 | 数值 |
|---|---:|
| CRR | 233/240 = 97.1% |
| Final Goal | 233/240 = 97.1% |
| VGF | 0/240 = 0% |
| pass-but-wrong | 0/240 |
| prefix mutation | 0/192 |
| 修复模型调用 | 268 次，平均 1.117 次/点 |

路由分布：

| 路由 | 数量 |
|---|---:|
| R2 直接 | 116 |
| `R2 → R1_FROM_STATE` | 28 |
| `R2 → R1` | 48 |
| `DETERMINISTIC_TRUNCATION` | 48 |

按压力类型的 Final Goal：

| 压力类型 | Final Goal | VGF |
|---|---:|---:|
| duplicate_pick_after_prefix | 48/48 | 0 |
| unknown_object_after_prefix | 48/48 | 0 |
| invalid_target_after_prefix | 48/48 | 0 |
| place_before_pick | 41/48 | 0 |
| repeat_pick_after_valid_plan | 48/48 | 0 |

剩余 7 个失败全部出现在 `place_before_pick`：R2 失败后回退 R1，
但 R1 返回了以 pick 结束的不完整计划，Validator 给出
`SCHEMA_ERROR`。它们不是 VGF，也未修改任何锁定前缀。

对照 v10 预注册标准，formal v11 通过：

1. `repeat_pick_after_valid_plan` Final Goal 100% ≥ 95%；
2. ROUTED 总体 Final Goal 97.1% ≥ 95%；
3. VGF 0% ≤ 2%；
4. prefix mutation 0/192；
5. 回退语义保持不变；
6. 确定性截断仍为 0 次 VLM 调用。

机器可读汇总见
`data/reports/formal_v11_routed_metrics_20260912.json`。

## 12. MetaWorld 仿真执行接口 smoke

在 formal v11 之后，本项目补充了任务级计划到机器人仿真执行的桥接验证。
目标不是重新训练策略，也不是声明完成完整真机闭环，而是验证：
`ModelPlan → Validator → Compiler → ExecutablePlan → MetaWorld executor`
这一接口链路是否可执行。

当前仿真环境使用 `metaworld-pick-place-v3`，验证范围限定为单物体
pick/place。执行适配器位于 `ch3/execution/metaworld_executor.py`，
它只接受由 `compile_plan()` 产生的 `ExecutablePlan`，并将
`adflow_grasp/adflow_place` 映射到 MetaWorld 的内置 expert policy。
符号对象和目标在当前 smoke 中映射到仿真环境的单一 puck 与 goal。

两层 smoke 均通过：

1. 环境层 smoke：直接验证 MetaWorld expert policy 执行闭环，
   3/3 success。
2. 计划桥接 smoke：先使用 Validator 校验一条手工冻结的
   `pick(red_cube_0) → place(red_cube_0, tray_1)` 计划，再编译执行。
   3/3 success，每个 episode 的两个 primitive 均成功，最终
   puck-goal 距离约为 0.073–0.076 m。

结果文件：

- `data/collections/sim_metaworld_pickplace_smoke_20260912_124638.json`
- `data/collections/sim_metaworld_plan_execution_20260912_125605.json`

数据边界：

- 当前 smoke 不调用 VLM，不产生新的 API 消耗；
- 当前使用 MetaWorld expert policy 完成底层运动，不等价于 AD-Flow 真机策略；
- 当前只验证单物体 pick/place 的执行接口，不是多任务仿真 benchmark；
- 该结果可用于说明“任务级计划接口可以映射到仿真 primitive”，但不能
  直接声明系统已经完成通用仿真泛化或真机部署。
