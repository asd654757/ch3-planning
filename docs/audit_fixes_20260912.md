# 审计修复记录（2026-09-12）

依据 GPT 外部审计意见逐条核实后的 P0 修复。备份：`backups/20260912_125814_p0_audit_fixes/`。

## 1. Windows 测试编码问题（已修复）

`tests/test_repair_pressure.py`、`tests/test_vlm_pipeline.py`、`tests/test_task_generator.py`
中共 5 处 `read_text()` 未指定编码，Windows 默认 GBK 读取 UTF-8 JSONL 失败。
全部补上 `encoding="utf-8"`。修复后 pytest 70/70 通过。

## 2. 压力测试 baseline 解析失败崩溃（已修复）

`scripts/repair_pressure.py`：`evaluate_plan` 在 plan 为 None 时返回
`(None, ...)`，原代码 `if not source_validation.valid` 会触发
`AttributeError`。改为先判断 `source_validation is None`。
注：formal_v8 的 48 个 baseline 全部成功，该 bug 未影响已有数据。

## 3. 指标口径修正（已修复）

### `ch3/metrics/metrics.py` `compute_system_metrics`
- **IDR**：不再返回隐式 `1.0`，改为显式
  `{detected, total_invalid, rate, note}`。rate 是确定性闭世界校验器的
  构造性结果（100%），不能解释为对未知错误的采样检出率。
- **FRR**：改为 `None` + `FRR_note`。当前数据流中修复只在初始计划非法时
  触发，"合法计划被误修为非法"没有观测机会，返回 0.0 是误导。
- **Final_Task_Ready**：改为系统级口径：
  `(初始合法且达标 + 初始非法经修复后达标) / n_tasks`，通过
  `(task_id, seed)` 将 repair 记录 join 回初始计划计算。原
  `goal_after / n_repair` 只能称为 `GSR_after_repair`（该字段保留）。

### `ch3/metrics/repair_pressure_metrics.py`
- 原字段名 "FRR" 的实际算法是 `valid and not goal_satisfied`
  （修复后合法但未达目标），与 FRR（合法计划被误修为非法）语义不符。
  更名为 **VGF（valid-but-goal-fail）**，同步更新
  `scripts/compare_external_baselines.py` 的汇总键与 CSV 表头，
  以及测试断言。

## 4. 外部基线 token 统计（已修复）

`scripts/compare_external_baselines.py` 原 total_tokens 公式化简后等价于
只统计 `baseline_total_tokens`，导致 formal R0/R1/R2 的 token 显示为 0。
改为：有 `baseline_total_tokens` 用之，否则用 `total_tokens`。
重新生成的 `results/table4_external_baselines_overall.csv`：

| Method | n | CRR | GSR | VGF | tokens |
|---|---|---|---|---|---|
| R0 | 240 | 41.3% | 41.3% | 0.0% | 242,248 |
| R1 | 240 | 100.0% | 99.2% | 0.8% | 419,859 |
| R2 | 240 | 55.8% | 55.8% | 0.0% | 399,193 |
| self_refine | 240 | 57.1% | 55.8% | 1.3% | 291,264 |
| checker_loop | 240 | 79.6% | 79.6% | 0.0% | 396,693 |

与 `docs/experiment_section_draft_20260911.md` 中的数字一致。

## 5. R2 前缀终态感知（代码已修复，实验待重跑）

`ch3/vlm/repair.py`：R2 的 prompt_input 新增 `prefix_final_state`
（`validation.final_state` 经 facts + empty_hand_facts 展开），即
**合法前缀执行后的状态**。模型可据此知道前缀结束时机械臂是否持物，
避免继续生成非法 pick。R0/R1 未改动，保持 frozen 协议可比。

**重要**：此改动改变了 R2 的 prompt hash。新 R2 结果不能与
formal_v8 的 R2 直接混合比较。下一步应先跑 6 任务 R2-only pilot
（约 30 次调用），观察 `unknown_object_after_prefix` 与
`duplicate_pick_after_prefix` 是否改善，再决定是否重跑 R2 正式
（240 次调用）。

## 6. B2a/B2b 处理方案（文档说明）

B2a/B2b 不需要重跑模型：它们是"共享初始计划 + 系统后处理"的派生臂，
可离线从 formal_v7 记录推导：
- **B2a**（校验拦截即停）：初始合法 → Final_Task_Ready = 初始 goal_ok；
  初始非法 → 0。
- **B2b**（校验后修复）：初始合法 → 同 B2a；初始非法 → 按修复策略的
  goal_ok。
论文中需明确写为"派生评估（derived arms）"，不声称独立采集。

## 7. 已核实但本轮不改的事项

- 双臂交错 pick/pick-place/place 被 prompt 约束 5 禁止（模拟器支持）：
  改动会破坏 formal_v8 冻结协议，写入论文讨论/未来工作。
- `extra="forbid"`、ValidatedPlan 编译边界、forbidden_objects、
  错误类型自适应修复路由：留作后续增强。
