# formal_v6 正式结果与 v5 对照

- 状态：**formal_v6 已冻结，不要重跑/覆盖**。
- 采集文件：`data/collections/formal_v6_flash_unique_descriptions.jsonl`
- 规模：55 tasks × 5 seeds；记录数 775（B0 275 + B1 275 + R0/R1/R2 各 75）。
- 模型：`qwen3-vl-flash`；根因修复：场景内 (color, shape) 描述唯一。

## 1. 总体结论

1. **描述歧义确实是 hard 任务的主要混淆变量。** 消歧后 hard 的 B0/B1 都从部分失败变为 175/175 全部 valid+goal。
2. **v6 中 B0 直连在总体上强于 B1**（GSR 90.9% vs 72.7%）。这个差距完全由 easy 的 B1 prompt/输出形态缺陷造成：50/50 easy B1 只输出 pick，不输出对应 place，因此 schema invalid。medium/hard 中 B0 与 B1 都全对。
3. **R1/R2 在 easy 修复上仍然有效**：R0 0/50，R1/R2 都把 50/50 easy 修到 valid+goal。
4. **不能宣称“结构化+修正全面优于直连”**。v6 的更稳妥结论是：描述歧义曾严重压制直连和结构化方法；消歧后两者在 medium/hard 上无差异，B1 的 easy prompt 还有系统性缺陷。
5. **infeasible 的 pass-but-wrong 风险暴露了**：R1 有 5/25、R2 有 4/25 生成 valid=true 但 goal=false 的计划。这些计划通过语法/对象/能力/状态校验但没有满足不可行任务预期，需要后续加入不可行性/目标可达性判定。

## 2. v5 → v6 同任务同 seed 配对提升

### B0 Direct

| scope / metric | n | v5 | v6 | v5 only | v6 only | both | exact p |
|---|---:|---:|---:|---:|---:|---:|---:|
| easy valid | 50 | 50 | 50 | 0 | 0 | 50 | 1 |
| easy goal | 50 | 50 | 50 | 0 | 0 | 50 | 1 |
| medium valid | 25 | 25 | 25 | 0 | 0 | 25 | 1 |
| medium goal | 25 | 25 | 25 | 0 | 0 | 25 | 1 |
| hard valid | 175 | 131 | 175 | 0 | 44 | 131 | 1.14e-13 |
| hard goal | 175 | 98 | 175 | 0 | 77 | 98 | 1.32e-23 |
| feasible valid | 275 | 206 | 250 | 0 | 44 | 206 | 1.14e-13 |
| feasible goal | 275 | 173 | 250 | 0 | 77 | 173 | 1.32e-23 |

### B1 Shared Structured

| scope / metric | n | v5 | v6 | v5 only | v6 only | both | exact p |
|---|---:|---:|---:|---:|---:|---:|---:|
| easy valid | 50 | 0 | 0 | 0 | 0 | 0 | 1 |
| easy goal | 50 | 0 | 0 | 0 | 0 | 0 | 1 |
| medium valid | 25 | 25 | 25 | 0 | 0 | 25 | 1 |
| medium goal | 25 | 25 | 25 | 0 | 0 | 25 | 1 |
| hard valid | 175 | 113 | 175 | 0 | 62 | 113 | 4.34e-19 |
| hard goal | 175 | 79 | 175 | 0 | 96 | 79 | 2.52e-29 |
| feasible valid | 275 | 138 | 200 | 0 | 62 | 138 | 4.34e-19 |
| feasible goal | 275 | 104 | 200 | 0 | 96 | 104 | 2.52e-29 |

重点：hard B1 goal 从 79/175 提升到 175/175（96 个同 seed 配对翻转，p=2.52e-29）；hard B0 goal 从 98/175 提升到 175/175（77 个配对翻转，p=1.32e-23）。

## 3. 修复层按难度拆分

| difficulty | mode | n | valid | goal | pass_but_wrong |
|---|---:|---:|---:|---:|---:|
| easy | R0 | 50 | 0 | 0 | 0 |
| easy | R1 | 50 | 50 | 50 | 0 |
| easy | R2 | 50 | 50 | 50 | 0 |
| infeasible | R0 | 25 | 0 | 0 | 0 |
| infeasible | R1 | 25 | 5 | 0 | 5 |
| infeasible | R2 | 25 | 4 | 0 | 4 |

v6 中 hard 没有触发修复（hard B1 全部 valid+goal），所以 R0/R1/R2 的 75 条记录来自 easy 失败与 infeasible 任务，而不是 hard。

## 4. 对论文叙事的含义

- 可以把 v5 → v6 作为 **混淆变量消融**：v5 的描述歧义使多条指令可能映射到同一物体，v6 保证场景内描述唯一。
- 这个消融显示，此前“结构化方法在 hard 上更差”主要是任务歧义/prompt/闭世界映射交互导致的，而不是结构化表示本身必然劣势。
- 但 v6 也削弱了“结构化比直连更强”的主张：消歧后 B0 在 medium/hard 与 B1 一样好。论文应把贡献转向 **验证器定位错误、前缀保留修复，以及歧义消融揭示的失败机制**。
- easy B1 的 0/50 必须作为已知缺陷报告。它可修复，但当前冻结数据里它是系统性的，不能隐藏。
- infeasible R1/R2 的 pass_but_wrong 应作为安全边界/验证器盲区单独讨论。

## 5. 下一版建议（不在本轮冻结数据上改）

1. 修 B1 easy prompt/输出约束：要求每个 pick 立即返回对应 place；可加入最小 pick-place 对示例。
2. 给不可行任务加显式可达性/不可行性判断，或让 repair 接受 `INFEASIBLE` 响应，而不是尝试构造可执行计划。
3. 若下一轮继续，最小改动是 v7：只修 easy B1 prompt 和 infeasible 响应协议，不动任务分布。

