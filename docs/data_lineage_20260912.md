# 数据血缘与实验定位（2026-09-12）

本文档固定各阶段数据能否用于论文。**旧数据文件只读，不覆盖、不重跑同名输出。**

## 正式数据（论文可用）

| 数据 | 文件 | 定位 | 用途 |
|---|---|---|---|
| formal_v7 | `data/collections/formal_v7_flash_prompt_protocol_20260911.jsonl` | 正式 | 闭世界规划、结构化输出与不可行拒绝验证 |
| external baselines | `data/collections/external_baseline_formal_20260911_173240.jsonl` | 正式 | 协议级 Self-Refine / Checker-loop 对比 |
| formal_v9 ROUTED | `data/collections/repair_pressure_formal_v9_routed_20260912_142533.jsonl` | 正式 | 240 压力点；R2 优先 + R1_FROM_STATE/R1 回退；CRR 92.9%，Final Goal 89.6%，VGF 3.3% |

## v10 代码增强（尚未采集 formal）

2026-09-12 已实现确定性尾部截断和 R1_FROM_STATE 空后缀提示词修正：

- 触发条件：合法前缀非空、前缀终态满足全部 goal facts、双臂空；
- 路由标记：`DETERMINISTIC_TRUNCATION`；
- 该路径不调用 VLM；
- 结果仍需通过 Validator 和 Goal Checker；
- 相关测试：`78 passed`。

因为 `repair.md` 的 prompt hash 发生变化，formal v10 必须使用新时间戳
采集，不得覆盖或与 formal_v9 混合。

## Legacy / 诊断数据（不与新 R2 结果混合）

| 数据 | 文件 | 定位 | 说明 |
|---|---|---|---|
| formal_v8（含 oracle） | `data/collections/repair_pressure_formal_v8_20260911_064919.jsonl` | legacy | 早期压力协议，R2 无状态感知输入 |
| formal_v8 no-oracle | `data/collections/repair_pressure_formal_v8_no_oracle_20260911_153655.jsonl` | legacy + 冻结 baseline 源 | 旧式 R2/R0/R1 压力结果只作诊断；其中的 `pressure_source_plan` 可复用为冻结 baseline |

## v10 source-goal 修正（尚未采集 formal）

v10 在压力 runner 中新增硬准入：压力源必须同时通过 Validator 和
Goal Checker。审计确认 formal_v8/v9 的 48 个冻结源中只有
`exclusion_constraint_012` 的 3 个 seed 不满足该条件；原因是其 baseline
只完成白楔子放入白碗，没有完成红球放到红托盘的目标。

formal v10 使用以下冻结源：

| 文件 | 状态 |
|---|---|
| `data/scenarios/stress_tasks_v8_pilot_v3_source_goal.jsonl` | v3 任务集；修正 `exclusion_constraint_012` 的 instruction 与 goal 不一致 |
| `data/collections/frozen_sources_v10_20260912_071103.jsonl` | 48 个 task/seed；45 个复用 formal_v8，`exclusion_constraint_012` 的 3 个 seed 在 v3 下重新生成且首次即通过双检 |
| `data/collections/repair_pressure_v10_source_goal_pilot_20260912_071330.jsonl` | 48 点 repeat_pick pilot：48/48 Final Goal，VGF 0，全走 `DETERMINISTIC_TRUNCATION` |

## R2 状态感知 pilot

| 数据 | 文件 | 定位 | 说明 |
|---|---|---|---|
| pilot 1 | `data/collections/repair_pressure_r2_state_aware_pilot_20260912_132529.jsonl` | **作废** | 发现 pipeline defect：对象层失败时 `final_state=None`，R2 prompt 缺少 `prefix_final_state` 等字段 |
| pilot 2 | `data/collections/repair_pressure_r2_state_aware_pilot2_20260912_133131.jsonl` | 机制验证，不作为正式性能结论 | 修复 pipeline 后，36 例 R2-only。状态层 duplicate 100%；unknown 39% goal，5 例 VGF 全为空后缀/不完整目标 |

## ROUTED pilot（错误类型感知路由）

| 数据 | 文件 | 定位 | 说明 |
|---|---|---|---|
| routed pilot v1 | `data/collections/repair_pressure_routed_pilot_20260912_134946.jsonl` | **fallback 语义修正前的诊断数据** | Router 机制有效，但 fallback 使用普通 R1/完整计划，不符合执行中安全边界；不得作为正式结果 |
| routed state pilot | `data/collections/repair_pressure_routed_state_pilot_20260912_141702.jsonl` | 当前有效机制验证 | 36 例；`R2 -> R1_FROM_STATE`；CRR/Final Goal 100%，VGF 0，fallback 11/36，总修复调用 47 |

### ROUTED v1 结果（仅诊断）

- CRR：36/36 = 100%
- Final Task Goal Rate / GSR after repair：36/36 = 100%
- VGF：0/36 = 0%
- pass-but-wrong：0/36
- 路由：R2 直接成功 23/36；R2 失败并回退普通 R1 成功 13/36
- 按压力：
  - duplicate_pick_after_prefix：18/18 goal，R2 直接成功 15，回退 3
  - unknown_object_after_prefix：18/18 goal，R2 直接成功 8，回退 10

### ROUTED state pilot 结果（当前有效机制验证）

- CRR：36/36 = 100%
- Final Task Goal Rate / GSR after repair：36/36 = 100%
- VGF：0/36 = 0%
- pass-but-wrong：0/36
- 路由：R2 直接成功 25/36；R1_FROM_STATE 回退成功 11/36
- 修复调用：47 次；平均 47/36 = 1.306 次/压力点
- 按压力：
  - duplicate_pick_after_prefix：18/18 goal，R2 直接成功 17，回退 1
  - unknown_object_after_prefix：18/18 goal，R2 直接成功 8，回退 10
- fallback 审计：11/11 达成目标；原始后缀无前缀动作重复；prompt 均含
  `prefix_final_state` / holding；无空后缀 VGF。

## Formal v9 ROUTED 结果摘要

- 采集文件：
  `data/collections/repair_pressure_formal_v9_routed_20260912_142533.jsonl`
- 机器可读汇总：
  `data/reports/formal_v9_routed_metrics_20260912.json`
- baseline：48/48 通过旧协议的 `Validator.valid`，复用 formal_v8
  no-oracle 冻结 baseline。v10 审计发现其中
  `exclusion_constraint_012` 的 3 个 seed 属于 state-valid 但
  goal-failing；formal_v9 的该子集因此标记为旧协议诊断边界，不算
  Goal Checker 失误；
- pressure points：240（16 任务 × 3 seeds × 5 压力）；
- CRR：223/240 = 92.9%；
- Final Goal / GSR after repair：215/240 = 89.6%；
- VGF：8/240 = 3.3%；
- pass-but-wrong：8/240；
- R2-only：140/240；
- `R2 → R1_FROM_STATE`：70/240；
- `R2 → R1`：30/240；
- 修复调用：340 次，平均 1.42 次/压力点；
- 主要失败簇：17 个 invalid 全部在
  `repeat_pick_after_valid_plan`，根因是“合法完整计划 + 冗余重复尾部”
  缺少程序化确定性截断；另有 8 例合法但未达成目标，集中在
  `invalid_target_after_prefix` 与 `unknown_object_after_prefix`。

## Formal v10 ROUTED 结果摘要

- 采集文件：
  `data/collections/repair_pressure_formal_v10_routed_20260912_071739.jsonl`
- 机器可读汇总：
  `data/reports/formal_v10_routed_metrics_20260912.json`
- pressure points：240；
- CRR：233/240 = 97.1%；
- Final Goal / GSR after repair：212/240 = 88.3%；
- VGF：21/240 = 8.75%；
- pass-but-wrong：21/240；
- prefix mutation：0/192；
- 路由：R2 直接 62；`R2 → R1_FROM_STATE` 82；`R2 → R1` 48；
  `DETERMINISTIC_TRUNCATION` 48；
- 修复调用：322 次，平均 1.34 次/压力点。

formal v10 证明确定性截断解决了 repeat_pick 冗余尾部失败簇，但
`R1_FROM_STATE` 出现“只完成当前 held object 的 place”的局部补全倾向，
导致 VGF 上升。因此 formal v10 不作为最终论文主结果。

## v11 required_transports 数据链

### Pilot 数据

| 数据 | 文件 | 定位 | 结果 |
|---|---|---|---|
| 目标失败 pilot | `data/collections/repair_pressure_required_transports_pilot_20260912_195941.jsonl` | 针对 v10 剩余 VGF 点 | 3/3 Final Goal，0 VGF，3/3 R2 直接 |
| 18 点复验 | `data/collections/repair_pressure_required_transports_18pt_20260912_200107.jsonl` | 复用 v10 最难子集做机制验证 | 18/18 Final Goal，0 VGF，R2 直接 16/18，`R2 → R1_FROM_STATE` 2/18 |

### Formal v11

| 文件 | 说明 |
|---|---|
| `data/collections/repair_pressure_formal_v11_routed_20260912_200252.jsonl` | formal v11 正式修复压力数据 |
| `logs/repair_pressure_formal_v11_routed_20260912_200252.log` | formal v11 运行日志 |
| `data/reports/formal_v11_routed_metrics_20260912.json` | formal v11 机器可读汇总 |

formal v11 复用 v10 冻结源
`frozen_sources_v10_20260912_071103.jsonl`。运行时尝试了 66 个
task/seed：其中 18 个逻辑冲突不可行源被 source-goal 双检拒绝并记录为
`pressure_source`；48 个可行源进入压力修复，产生 240 个
`repair_pressure` 点。18 个拒绝源不进入正式指标。

formal v11 结果：

- CRR：233/240 = 97.1%；
- Final Goal / GSR after repair：233/240 = 97.1%；
- VGF：0/240 = 0%；
- pass-but-wrong：0/240；
- prefix mutation：0/192；
- 路由：R2 直接 116；`R2 → R1_FROM_STATE` 28；`R2 → R1` 48；
  `DETERMINISTIC_TRUNCATION` 48；
- 修复调用：268 次，平均 1.117 次/压力点；
- 剩余失败：7 个 `place_before_pick`，全部是 `R2 → R1` 后 R1 返回
  以 pick 结束的不完整计划，Validator 给出 `SCHEMA_ERROR`。

formal v11 通过 v10 预注册标准，可作为 ROUTED 当前正式主结果。
formal v9/v10 保留为版本演化诊断数据，不得与 v11 混合统计。

## 当前数据边界

1. ROUTED pilot 不是 formal 数据；不用于主结果表的最终功效结论。
2. formal_v8 旧 R2 结果应标记 legacy，不与状态感知 R2 或 ROUTED 混合。
3. pilot 1 因 pipeline defect 作废，论文中可作为调试/审计证据，不作性能数据。
4. formal_v9 是无确定性截断、无 required_transports 的版本；formal_v10
   是确定性截断版本但未达 Final Goal/VGF 标准；两者保留为版本演化
   诊断数据。
5. formal_v11 是当前正式 ROUTED 主结果。后续若再修改 prompt、路由、
   validator 或状态推导，必须另建 formal v12，使用新时间戳输出与
   冻结 baseline；不得覆盖、筛选后覆盖或与旧版本混合。

## MetaWorld 仿真执行 smoke

formal v11 之后新增了两层仿真接入验证：

| 文件 | 说明 |
|---|---|
| `data/collections/sim_metaworld_pickplace_smoke_20260912_124638.json` | 环境层 expert-policy smoke：3/3 success |
| `data/collections/sim_metaworld_plan_execution_20260912_125605.json` | 计划桥接 smoke：`ModelPlan → Validator → Compiler → ExecutablePlan → MetaWorld`，3/3 success |

桥接 smoke 使用一条冻结的合法计划
`pick(red_cube_0, right) → place(red_cube_0, tray_1, right)`。
适配器 `ch3/execution/metaworld_executor.py` 将编译后的
`adflow_grasp/adflow_place` 映射到 `metaworld-pick-place-v3` 的内置
expert policy。该数据不调用 VLM。

数据边界：这是单物体 pick/place 的接口级验证，不是仿真 benchmark，
也不是 AD-Flow 真机策略验证。
