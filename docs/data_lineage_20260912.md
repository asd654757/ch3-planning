# 数据血缘与实验定位（2026-09-12）

本文档固定各阶段数据能否用于论文。**旧数据文件只读，不覆盖、不重跑同名输出。**

## 正式数据（论文可用）

| 数据 | 文件 | 定位 | 用途 |
|---|---|---|---|
| formal_v7 | `data/collections/formal_v7_flash_prompt_protocol_20260911.jsonl` | 正式 | 闭世界规划、结构化输出与不可行拒绝验证 |
| external baselines | `data/collections/external_baseline_formal_20260911_173240.jsonl` | 正式 | 协议级 Self-Refine / Checker-loop 对比 |
| formal_v9 ROUTED | `data/collections/repair_pressure_formal_v9_routed_20260912_142533.jsonl` | 正式 | 240 压力点；R2 优先 + R1_FROM_STATE/R1 回退；CRR 92.9%，Final Goal 89.6%，VGF 3.3% |

## Legacy / 诊断数据（不与新 R2 结果混合）

| 数据 | 文件 | 定位 | 说明 |
|---|---|---|---|
| formal_v8（含 oracle） | `data/collections/repair_pressure_formal_v8_20260911_064919.jsonl` | legacy | 早期压力协议，R2 无状态感知输入 |
| formal_v8 no-oracle | `data/collections/repair_pressure_formal_v8_no_oracle_20260911_153655.jsonl` | legacy + 冻结 baseline 源 | 旧式 R2/R0/R1 压力结果只作诊断；其中的 `pressure_source_plan` 可复用为冻结 baseline |

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
- baseline：48/48 有效，复用 formal_v8 no-oracle 冻结 baseline；
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

## 当前数据边界

1. ROUTED pilot 不是 formal v9；不用于主结果表的最终功效结论。
2. formal_v8 旧 R2 结果应标记 legacy，不与状态感知 R2 或 ROUTED 混合。
3. pilot 1 因 pipeline defect 作废，论文中可作为调试/审计证据，不作性能数据。
4. formal_v9 已成为当前正式 ROUTED 数据。后续若加入确定性尾部截断
   或修改 R1_FROM_STATE prompt，必须作为 formal v10 重新采集，使用新
   时间戳输出、冻结 baseline，并另建版本记录；不得覆盖或混合 formal_v9。
