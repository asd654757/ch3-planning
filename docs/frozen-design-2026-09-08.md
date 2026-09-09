# 阶段 0 冻结设计（2026-09-08 定稿）

来源：《第三章两用实施清单_2026-09.md》（含 0908 修订）与《第三章思路.md》。本文件是代码实现的唯一协议；与此冲突处以此为准。

## 1. 范围（不做）

RAG / 记忆 / 多 Agent / PDDL / 场景图 / 物体检测精度 / 完整 VLA / TAMP / BT 合成 / 独立 VLM success detector——均不进入第三章实现。

## 2. 术语

- 任务/计划层统一 **pick/place**；AD-Flow 底层原语 grasp/place 只在 `capability_registry.yaml` 的 policy 映射与执行器接口出现。
- 技能集合：`pick(object_id, arm)`、`place(object_id, target_id, arm)`。
- 对象 id 用稳定字符串；目标位（target）可为 `table` 或场景内可放置物体 id。

## 3. 表示与接口

- ModelPlan JSON 字段：`skill / object_id / target_id / arm / step_id`。
- 状态推进单一接口：`step(state, action) -> (next_state, success | error)`；离线=状态模拟器，真机=Execution Feedback Interface。
- Execution Feedback：`executor.execute(action) -> SUCCESS | FAILED | UNKNOWN`，结合夹爪/位姿/AD-Flow 返回/简单视觉确认；FAILED/UNKNOWN → stop 剩余计划并归因；不允许默认按成功推进。
- Validator 为纯函数评测器（evaluator）；四层：syntax → object(闭世界) → capability → state。
- 错误码固定：E01 SCHEMA_ERROR / E02 UNKNOWN_OBJECT / E03 UNREGISTERED_SKILL / E04 MISSING_PARAMETER / E05 ARM_NOT_EMPTY / E06 OBJECT_NOT_HELD / E07 TARGET_NOT_FOUND / E08 STATE_TRANSITION_ERROR。

## 4. 实验协议（冻结）

- 实验 A 四臂：B0 Direct（自由文本，独立生成+解析器）/ B1 Structured（P 直接用）/ B2a +Validation（P→Validator，不过 Safe Stop）/ B2b Ours（P→Validator→一次前缀保留修正）。
- **B1/B2a/B2b 共享同一次 VLM 初始 ModelPlan P**（一次生成、三路分发）；B0 单独生成。
- 指标分两组：
  - 组1 ModelPlan 质量（B0/B1）：FVR / CVR / EPR / GSR；
  - 组2 系统行为（B1/B2a/B2b）：IDR / CRR / FRR / Final Task-Ready / 修复后 GSR / LLM calls / latency。
- Repair Success = 修复后 plan 通过完整校验 ∧ goal ⊆ S_T；只过校验不过目标 → `pass-but-wrong` 单列。
- 修复对比三组：R0 Retry from Scratch / R1 Full-Plan Repair / R2 Ours（R1/R2 输入相同，只差全计划 vs 前缀保留）。
- 真机指标：PAR / IDR / TSR + per-step execution feedback；真机只做集成验证。
- 任务难度：规则化生成（Easy/Medium/Hard/Infeasible）；pilot 只查天花板，不挑任务。
- 统计：配对设计 + McNemar；每任务按 seed 采 ≥5 个独立 P。

## 5. 模块结构（与代码对应）

```
ch3/schema          ModelPlan 动作/计划/目标 + JSON Schema
ch3/capability      能力注册表（YAML + registry 加载）
ch3/state           离散世界状态、模拟器 step、Execution Feedback
ch3/validator       四层校验 + 首错定位（Evaluator）
ch3/repair          R2 程序侧前缀保护与合并
ch3/compiler        ModelPlan → ExecutablePlan（补 policy_id）
ch3/goal            goal ⊆ S_T 判定
ch3/logger          episode JSON 日志
ch3/protocols       冻结常量与协议类型（B/R/错误码/RepairSuccess）
```

## 6. 阶段状态

- [x] 阶段0 冻结设计（本文件 + capability_registry.yaml + schema）
- [x] 阶段1 Validator + 状态模拟器（含注入用例回归）——骨架已实现，20+ 测试通过；接 VLM/真机在阶段2/4
- [x] 阶段2 一次前缀保护修正（R2 程序侧 `ch3/repair/prefix_guard.py` + VLM R2 调用/采集链路）
- [ ] 阶段3 离线任务集与规划实验
