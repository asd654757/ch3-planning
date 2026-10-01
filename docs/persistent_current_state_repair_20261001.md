# 持续场景中的现有 R1_FROM_STATE 接入

## 接口与边界

当前语言场景桥接模块新增可选一次 `PlanRepairer.repair(repair_mode="R1_FROM_STATE")`。触发条件为模型剩余计划已解析但未通过 Validator 或模拟目标检查。格式解析失败目前仍直接停止，未进入该回退。

调用起点为最新持物证据形成的当前状态，局部 validated_prefix 为空，不模拟已执行抓取作为当前状态。能力与任务仅限右臂蓝色方块到绿色区域，场景建模假设仍存在。修复输出再次校验与目标检查，最终仅一条 place 允许执行，不能用固定计划替代被拒绝模型输出。

这次接入的是现有 R1_FROM_STATE，不是完整 ROUTED，也没有 R2。旧 repair prompt 中仍存在双臂状态字段，任务指令限定右臂且最终 Validator 使用仅右臂注册表，其他臂动作会被拒绝；未来应将提示能力字段全面改为后端实际注册表。

## 受控错误的实际执行验证

指令：“把蓝色方块放到绿色区域。”先由模型解析目标，再执行脚本初始 pick。当前在线持物证据通过后模型生成剩余 place。

本次原始模型输出实际上是正确 place；测试显式把其目标改成 `injected_unknown_region`，日志同时保存原始输出与注入后的计划，不能写成模型自然输出错误。

开启回退的记录：`data/collections/multiobject_current_state_repair_20261001_v1/`。

- 原始输出正确；注入后 Validator 报 UNKNOWN_OBJECT。
- 无非法动作被下发。
- 实际调用现有 R1_FROM_STATE，输入当前 holding 状态，生成新的合法 place。
- 再校验通过后，在原持续场景执行放置，独立评分成功。
- 3 次模型调用：目标、剩余计划、修复各一次；一次初始抓取，不回放 pick，不重置场景。

关闭回退的同协议记录：`data/collections/multiobject_current_state_repair_disabled_20261001_v1/`。未知目标同样被拒绝，停止，不执行放置。

这是一组小规模受控机制比较，不是自然错误恢复率、显著性或整体方法性能证明。启用回退多一次调用，未来正式比较要明确预算差异。

## 日志

`remaining_plan_validation.json` 保存原始模型计划、实际评估计划、注入标志、首错、修复起点和修复输出。`remaining_repair_call.json` 保存修复原响应、prompt、token 和时间。真值评分只用于最终评估，不用于修复起点。

首次 enabled 产物 scope 文案仍是旧的 no R1/R2 字符串；其 repair_audit 和独立修复调用准确记录实际 R1_FROM_STATE 执行。后续已修正脚本文案，旧记录保留。

## 下一步

不再扩展单一 place 的受控演示。优先将能力字段和实际注册表统一，接通自然失败反馈到 task/state 入口，再冻结少量多步持续场景任务。初始 pick 仍由脚本安排，需与完整初始规划区分。未知状态继续优先观察/拒绝，不能以默认空手或模拟前缀替代。

相关自动测试 73 项通过。无后台正式实验。
