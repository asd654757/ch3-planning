# 数据血缘与实验定位（2026-09-12）

本文档固定各阶段数据能否用于论文。**旧数据文件只读，不覆盖、不重跑同名输出。**

## 正式数据（论文可用）

| 数据 | 文件 | 定位 | 用途 |
|---|---|---|---|
| formal_v7 | `data/collections/formal_v7_flash_prompt_protocol_20260911.jsonl` | 正式 | 闭世界规划、结构化输出与不可行拒绝验证 |
| external baselines | `data/collections/external_baseline_formal_20260911_173240.jsonl` | 正式 | 协议级 Self-Refine / Checker-loop 对比 |

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
| routed pilot | `data/collections/repair_pressure_routed_pilot_20260912_134946.jsonl` | 系统级路由验证，不写成大样本正式结论 | 6 任务 × 3 seeds × 2 压力 = 36 例；冻结 baseline 全部复用，0 次 baseline 调用 |

### ROUTED 结果

- CRR：36/36 = 100%
- Final Task Goal Rate / GSR after repair：36/36 = 100%
- VGF：0/36 = 0%
- pass-but-wrong：0/36
- 路由：R2 直接成功 23/36；R2 失败并回退 R1 成功 13/36
- 按压力：
  - duplicate_pick_after_prefix：18/18 goal，R2 直接成功 15，回退 3
  - unknown_object_after_prefix：18/18 goal，R2 直接成功 8，回退 10

### 解释

ROUTED 的系统级 100% 不能解释为“R2 在所有错误上 100%”。准确表述是：

> 分层路由把状态感知后缀恢复与完整重规划回退组合后，在该 36 例受控 pilot 中恢复了全部任务目标；其中状态层错误多数由 R2 直接完成，超出 R2 边界的对象引用错误主要依赖 R1 回退。

## 当前数据边界

1. ROUTED pilot 不是 formal v9；不用于主结果表的最终功效结论。
2. formal_v8 旧 R2 结果应标记 legacy，不与状态感知 R2 或 ROUTED 混合。
3. pilot 1 因 pipeline defect 作废，论文中可作为调试/审计证据，不作性能数据。
4. 后续若重跑 formal v9，必须使用新的时间戳输出、冻结 baseline、状态感知 prompt 和 ROUTED 协议，并另建版本记录。
