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

## formal v11 修复计划仿真 pilot

| 文件 | 说明 |
|---|---|
| `data/collections/sim_pilot_from_formal_v11_20260912_132251.json` | 从 formal v11 抽取 7 条 ROUTED 成功计划做 MetaWorld 接口执行 pilot |
| `logs/sim_pilot_from_formal_v11_20260912_132251.log` | 对应运行日志 |
| `logs/sim_pilot_from_formal_v11_20260912_131553.log` | 首次启动失败的诊断日志；因 `ValidationResult.error_layer` 字段名错误终止 |

pilot 源为
`data/collections/repair_pressure_formal_v11_routed_20260912_200252.jsonl`。
选样覆盖 5 类压力与主要路由模式，共 7 条计划、28 个
`pick/place` 对。执行协议是
`one_pick_place_pair_per_fresh_meta_world_episode`，不调用 VLM。

结果：

- 对级成功：27/28 = 96.43%；
- 全对成功 case：6/7 = 85.71%；
- 唯一失败是 case 6 的 grasp primitive，属于 MetaWorld expert
  policy 执行失败，不是计划修复或 Validator 失败。

边界：该数据只支持“修复后计划可映射到仿真 primitive 并执行”的
接口级结论，不能与 formal v11 的 240 点修复压力结果混合计算。

## 四臂配对仿真对照 pilot

| 文件 | 说明 |
|---|---|
| `data/collections/sim_compare_baselines_20260912_134818.json` | 20 个配对点上 NO_REPAIR / R2_ONLY / CHECKER_LOOP / ROUTED 的冻结计划仿真对照 |
| `logs/sim_compare_baselines_20260912_134332.log` | 对应运行日志 |
| `data/reports/sim_compare_baselines_20260912_134818_analysis.json` | 派生分析：按臂汇总、配对差异和失败模式 |

对照点来自三个冻结源：

- formal v11 ROUTED 结果：
  `data/collections/repair_pressure_formal_v11_routed_20260912_200252.jsonl`；
- formal v8 no-oracle R2-only 结果：
  `data/collections/repair_pressure_formal_v8_no_oracle_20260911_153655.jsonl`；
- 外部基线 Checker-loop 结果：
  `data/collections/external_baseline_formal_20260911_173240.jsonl`。

抽样规则是 5 类压力 × 4 个不同任务，共 20 个配对点；所有臂在同一
`task_id / seed / pressure_type` 上配对。执行协议仍是
`one_pick_place_pair_per_fresh_meta_world_episode`，全程不调用 VLM。

终层汇总：

| 臂 | 最终成功 |
|---|---:|
| NO_REPAIR | 0/20 |
| R2_ONLY | 6/20 |
| CHECKER_LOOP | 16/20 |
| ROUTED | 15/20 |

ROUTED 与 CHECKER_LOOP 的配对差异为
`(14 both success, 3 both failure, 1 ROUTED-only, 2 CHECKER_LOOP-only)`，
精确 McNemar `p=1.000`。因此该 20 点仿真 pilot 不能用来声称 ROUTED
在仿真终层显著优于 Checker-loop。

数据边界：这是冻结修复计划的接口级仿真对照 pilot，只用于检查
修复计划能否进入同一执行接口；不能与 240 点符号层 formal v11
结果合并为一个“仿真 benchmark 成功率”，也不能外推为真机结论。

## 四臂配对仿真对照扩展（50 点）

| 文件 | 说明 |
|---|---|
| `data/collections/sim_compare_baselines_20260912_152744.json` | 50 个配对点上 NO_REPAIR / R2_ONLY / CHECKER_LOOP / ROUTED 的冻结计划仿真对照 |
| `logs/sim_compare_baselines_50points_20260912_151909.log` | 对应运行日志 |
| `data/reports/sim_compare_baselines_20260912_152744_analysis.json` | 派生分析：按臂汇总、按压力汇总、配对差异和置信区间 |

抽样规则扩展为 5 类压力 × 10 个不同任务，共 50 个配对点。
四臂来源、执行协议和符号层重校验规则与 20 点 pilot 保持一致；
20 点 pilot 文件
`data/collections/sim_compare_baselines_20260912_134818.json`
是该 50 点集合前 4 个任务子集的早期诊断结果。

终层结果：

| 臂 | 最终成功 |
|---|---:|
| NO_REPAIR | 0/50 |
| R2_ONLY | 16/50 |
| CHECKER_LOOP | 34/50 |
| ROUTED | 45/50 |

ROUTED 与 CHECKER_LOOP 的配对差异为
`(32 both success, 3 both failure, 13 ROUTED-only, 2 CHECKER_LOOP-only)`，
精确 McNemar `p=0.0074`。因此 50 点扩展结果支持 ROUTED 在
仿真终层显著优于 CHECKER_LOOP；20 点 pilot 的反向结果应视为
小样本波动。

数据边界：仍是冻结修复计划的接口级仿真对照，不是在线 VLM
仿真 benchmark，不能与 formal v11 的 240 点符号层结果合并，
也不能外推为真机结论。

## Sim-IF-240：240 点五臂配对仿真执行对照

| 文件 | 说明 |
|---|---|
| `data/collections/sim_compare_baselines_20260912_163449.json` | 240 个配对点上 NO_REPAIR / R2_ONLY / SELF_REFINE / CHECKER_LOOP / ROUTED 的冻结计划仿真对照 |
| `data/reports/sim_if_240_analysis_20260913.json` | 派生分析：终层汇总、按压力结果、精确 McNemar 和失败模式 |

抽样规则为 5 类压力 × 48 个冻结压力点，共 240 个
`task_id / seed / pressure_type` 配对点。五臂来源如下：

- ROUTED：
  `repair_pressure_formal_v11_routed_20260912_200252.jsonl`；
- R2-only：
  `repair_pressure_formal_v8_no_oracle_20260911_153655.jsonl`；
- Self-Refine 与 Checker-loop：
  `external_baseline_formal_20260911_173240.jsonl`；
- NO_REPAIR：同一配对键下的受压力污染初始计划。

执行协议保持为
`one_pick_place_pair_per_fresh_meta_world_episode`，全程不调用 VLM。
该结果是接口级仿真执行验证；不能与 formal v11 的 240 点符号层结果
合并为同一指标，也不能外推为真机闭环结论。

终层汇总：

| 臂 | 最终成功 |
|---|---:|
| NO_REPAIR | 0/240 |
| R2_ONLY | 97/240 |
| SELF_REFINE | 101/240 |
| CHECKER_LOOP | 151/240 |
| ROUTED | 200/240 |

ROUTED 与 CHECKER_LOOP 的配对差异为
`(125 both success, 14 both failure, 75 ROUTED-only, 26 CHECKER_LOOP-only)`，
精确 McNemar `p=1.1154954264353206e-06`。

## MultiSkill 接口扩展 smoke（push / press）

| 文件 | 说明 |
|---|---|
| `data/collections/sim_push_skill_smoke_20260913_130436.json` | `push` 符号计划经 Validator/Compiler 后在 `metaworld-push-v3` 中执行 3 次 |
| `data/collections/sim_button_press_skill_smoke_20260913_130700.json` | `press` 符号计划经 Validator/Compiler 后在 `metaworld-button-press-v3` 中执行 3 次 |
| `logs/sim_push_skill_smoke_20260913_130436.log` | 对应 push 运行日志 |
| `logs/sim_button_press_skill_smoke_20260913_130700.log` | 对应 press 运行日志 |

这两个文件属于接口级 smoke，不是 formal benchmark。它们验证：

- `Skill.PUSH` 与 `Skill.PRESS` 已进入 ModelPlan 闭集；
- 能力注册表可映射到 `metaworld_push` 与 `metaworld_button_press`；
- 状态事实支持 `pushed_to(...)` 与 `pressed(...)`；
- Validator、Goal Checker、Compiler 和 MetaWorld executor 全链路可用。

两者均不调用 VLM，均 3/3 成功，且 symbolic validation 与 symbolic
goal 均通过。

## MultiSkill-IF-v1：321 点仿真执行对照

| 文件 | 说明 |
|---|---|
| `data/collections/sim_compare_baselines_multiskill_20260913_065107.json` | 321 个配对点上 NO_REPAIR / ROUTED 的冻结计划仿真对照 |
| `data/reports/multiskill_sim_if_v1_analysis_20260913.json` | 派生分析：总表、技能族、压力类型和失败样本 |
| `logs/sim_compare_multiskill_formal_20260913_144500.log` | 对应仿真运行日志 |

符号层来源为
`repair_pressure_multiskill_formal_v1_20260913_140057.jsonl`。该 formal
集合包含 321 个有效 repair-pressure 记录：pick/place 108 点、push 108
点、press 105 点。press 少于 108 是因为有一个 baseline 在符号层被
Validator 正确拒绝，因此没有进入压力修复评测。

执行协议为：

- pick/place：一个 pick/place pair 在一个 fresh MetaWorld episode 中执行；
- push：一个 push 动作在 `metaworld-push-v3` 的 fresh episode 中执行；
- press：一个 press 动作在 `metaworld-button-press-v3` 的 fresh episode
  中执行。

全程不调用 VLM。`NO_REPAIR` 使用受污染 stress plan；因为其符号层全部
非法，所以不进入仿真执行，最终成功率为 0。这不是一个可执行的
real-world baseline，而是压力注入下的安全对照。

终层汇总：

| 臂 | 符号有效 | 目标满足 | 仿真尝试 | 仿真成功 | 最终成功 |
|---|---:|---:|---:|---:|---:|
| NO_REPAIR | 0/321 | 0/321 | 0/321 | 0/321 | 0/321 |
| ROUTED | 321/321 | 321/321 | 321/321 | 315/321 | 315/321 |

按技能族分解：

| 技能族 | ROUTED 最终成功 |
|---|---:|
| pick/place | 102/108 |
| push | 108/108 |
| press | 105/105 |

6 个失败全部位于 pick/place 的第一个 pick/place pair，且都在第一
grasp primitive 失败；没有失败来自符号校验、目标检查或路由回退。
因此这些是接口级仿真执行失败，不应解释为 MultiSkill 修复路由失败。

## MultiSkill 外部基线 formal：Self-Refine 与 Checker-loop

| 文件 | 说明 |
|---|---|
| `data/collections/external_baseline_multiskill_formal_v1_20260913_145938.jsonl` | 321 个配对压力点上 Self-Refine 与 Checker-loop 的符号层 formal 结果，共 642 条 |
| `data/reports/multiskill_external_baselines_20260913_072737_overall.csv` | 总表，含 CRR、GSR、VGF、pass-but-wrong、token 与平均轮次 |
| `data/reports/multiskill_external_baselines_20260913_072737_by_pressure.csv` | 按压力类型分解表 |
| `data/reports/multiskill_external_baselines_20260913_072737_mcnemar.csv` | ROUTED 与两个外部基线的配对精确 McNemar 表 |
| `data/reports/multiskill_external_baselines_analysis_20260913_072821.json` | 派生审计报告，含技能族分解与失败模式 |
| `logs/external_baseline_multiskill_formal_v1_20260913_145938.log` | 对应运行日志 |

来源集合为 `repair_pressure_multiskill_formal_v1_20260913_140057.jsonl`。
配对键为 `task_id / seed / pressure_type`，共 321 点。运行参数为
Self-Refine 1 轮、Checker-loop 最多 2 轮。总 VLM 调用上限 963 次；
实际日志显示 `completed_cases: 642`，与两个基线各 321 点一致。

符号层结果：

| 臂 | CRR | GSR | VGF | pass-but-wrong | 平均轮次 | 基线总 token |
|---|---:|---:|---:|---:|---:|---:|
| ROUTED | 321/321 = 100.0% | 321/321 = 100.0% | 0 | 0 | 1.000 | 145,728 |
| Self-Refine | 106/321 = 33.0% | 106/321 = 33.0% | 0 | 0 | 1.000 | 199,601 |
| Checker-loop | 242/321 = 75.4% | 148/321 = 46.1% | 94 | 94 | 1.361 | 292,806 |

token 口径：Checker-loop 使用 `baseline_total_tokens`，因此包含
checker 反馈调用；Self-Refine 与 ROUTED 两种字段一致。

按技能族：

| 技能族 | ROUTED CRR/GSR | Self-Refine CRR/GSR | Checker-loop CRR/GSR |
|---|---:|---:|---:|
| pick/place | 108/108 | 105/108 | 108/108 |
| push | 108/108 | 0/108 | 105/108 CRR，11/108 GSR |
| press | 105/105 | 1/105 | 29/105 |

主要失败模式：

1. Self-Refine 在 push/press 上大量把任务误解为 pick/place，产生
   `INFEASIBLE_RESPONSE`；这解释了 push 0/108、press 1/105。
2. Checker-loop 在 push 上产生 94 个 pass-but-wrong：计划通过
   Validator，但没有满足 pushed-to 目标。它主要表现为“计划结构合法但
   任务未完成”，而不是统一报告成功。
3. Checker-loop 在 press 上常见 `ARM_NOT_EMPTY`，说明其反馈仍然难以
   稳定处理执行状态约束。

配对精确 McNemar（CRR 层）：

| 对比 | ROUTED 成功 | 基线成功 | only ROUTED | only baseline | p 值 |
|---|---:|---:|---:|---:|---:|
| ROUTED vs Self-Refine | 321/321 | 106/321 | 215 | 0 | 3.80e-65 |
| ROUTED vs Checker-loop | 321/321 | 242/321 | 79 | 0 | 3.31e-24 |

该结果是 MultiSkill 符号修复层 formal 对照，不调用仿真执行层；不能
与 `sim_compare_baselines_multiskill_20260913_065107.json` 的仿真
终层结果直接相加或平均。论文中应分别报告符号层 CRR/GSR 和仿真层
final success。

## MultiSkill-IF-v1：321 点四臂仿真执行对照

| 文件 | 说明 |
|---|---|
| `data/collections/sim_compare_baselines_multiskill_20260913_074334.json` | 321 个配对点上 NO_REPAIR / SELF_REFINE / CHECKER_LOOP / ROUTED 的冻结计划仿真对照 |
| `data/reports/multiskill_sim_fourarm_analysis_20260913_075732.json` | 派生分析：终层汇总、进入仿真后的条件成功率、技能族分解、压力类型分解和 McNemar 检验 |
| `logs/sim_compare_multiskill_external_formal_20260913_073304.log` | 对应仿真运行日志 |

符号层来源有两个：ROUTED 来自
`repair_pressure_multiskill_formal_v1_20260913_140057.jsonl`；
SELF_REFINE 与 CHECKER_LOOP 来自
`external_baseline_multiskill_formal_v1_20260913_145938.jsonl`。
配对键仍为 `task_id / seed / pressure_type`，共 321 点。全程不调用
VLM；执行协议与 MultiSkill-IF-v1 一致，即 pick/place pair、单步 push
和单步 press 分别在 fresh MetaWorld episode 中执行。

终层汇总：

| 臂 | 符号有效 | 目标满足 | 仿真尝试 | 仿真成功 | final success |
|---|---:|---:|---:|---:|---:|
| NO_REPAIR | 0/321 | 0/321 | 0/321 | 0/321 | 0/321 = 0.0% |
| SELF_REFINE | 106/321 | 106/321 | 106/321 | 99/106 | 99/321 = 30.8% |
| CHECKER_LOOP | 242/321 | 148/321 | 148/321 | 138/148 | 138/321 = 43.0% |
| ROUTED | 321/321 | 321/321 | 321/321 | 315/321 | 315/321 = 98.1% |

进入仿真后的条件成功率：

| 臂 | sim success / attempted |
|---|---:|
| SELF_REFINE | 99/106 = 93.4% |
| CHECKER_LOOP | 138/148 = 93.2% |
| ROUTED | 315/321 = 98.1% |

这个条件成功率说明：外部基线与 ROUTED 的主要差距发生在符号修复层，
而不是 MetaWorld 底层执行层。Checker-loop 的 94 个 pass-but-wrong
计划没有进入仿真；Self-Refine 的 215 个非法计划也没有进入仿真。

按技能族的 final success：

| 技能族 | NO_REPAIR | SELF_REFINE | CHECKER_LOOP | ROUTED |
|---|---:|---:|---:|---:|
| pick/place | 0/108 | 98/108 | 98/108 | 102/108 |
| push | 0/108 | 0/108 | 11/108 | 108/108 |
| press | 0/105 | 1/105 | 29/105 | 105/105 |

终层精确 McNemar：

| 对比 | only 基线成功 | only ROUTED 成功 | p 值 |
|---|---:|---:|---:|
| SELF_REFINE vs ROUTED | 6 | 222 | 8.70e-58 |
| CHECKER_LOOP vs ROUTED | 6 | 183 | 1.54e-46 |

23 个仿真失败全部发生在 pick/place 的第一个 grasp primitive；
SELF_REFINE 有 7 个，CHECKER_LOOP 有 10 个，ROUTED 有 6 个。没有
push 或 press 仿真失败，也没有失败来自符号校验或目标检查。因此
ROUTED 的 6 个失败应解释为接口级执行容差或场景随机性问题，不应解释
为修复路由失败。
