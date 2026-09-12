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

## 3. 自适应修复路由（设计定稿，实现分步）

```
VLM Planner
→ ModelPlan
→ 四层 Validator
→ 修复路由器
   ├─ Deterministic Truncation（合法计划后仅冗余非法尾部，程序截断）
   ├─ Full Replanning / R1（执行前纠错）
   └─ State-aware Suffix Repair / R2（执行中恢复，持物状态感知）
→ R2 失败回退 R1（从当前状态规划剩余任务）
→ Goal Checker
→ ExecutablePlan
→ AD-Flow
```

实施顺序：先验证 R2 状态感知 pilot 达标，再实现路由器与确定性截断；
两者都通过后才构成完整方法。

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
