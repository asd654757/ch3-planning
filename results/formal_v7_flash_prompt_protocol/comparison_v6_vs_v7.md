# formal_v7 正式结果与 v6 对照

- 状态：**formal_v7 已冻结，不要重跑/覆盖**。
- 采集文件：`data/collections/formal_v7_flash_prompt_protocol_20260911.jsonl`
- SHA256：`d98f3f4d250867e86d2f951a7273adc69b40cb6dc04420e1df9f74b5cc92d8b0`
- 规模：55 tasks × 5 seeds；记录数 625（B0 275 + B1 275 + R0/R1/R2 各 25）。
- 模型：`qwen3-vl-flash`；max tokens：3072。
- 相对 v6 的改动：完整 pick-place prompt、结构化/B0 显式 infeasible 拒绝协议、确定性闭世界不可行性检查；不动任务分布。

## 1. 总体结论

1. **v6 中 easy B1 的系统性 schema 失败已修复。** easy B1 从 0/50 valid+goal 提升到 50/50；同任务同 seed 配对完全一致地翻转，exact McNemar p=1.78e-15。
2. **medium/hard 没有退化。** B0 和 B1 在 medium 的 25/25、hard 的 175/175 上均保持 valid+goal。
3. **infeasible 的 pass-but-wrong 从修复层消失。** v6 中 R1/R2 分别有 5/25 和 4/25 生成通过校验但不满足预期的计划；v7 中 R0/R1/R2 均为 25/25 显式拒绝，pbw=0。
4. **不可行任务的正确行为是显式拒绝，而不是尝试构造可执行计划。** v7 的 B0/B1 及三个 repair 分支均为 25/25 `INFEASIBLE_RESPONSE`；因此 conventional FVR/GSR 在 infeasible 子集上显示 0%，这是口径问题，不应解读为模型能力下降。
5. **不能把 v7 说成“修复层救回更多任务”。** 恰恰相反，easy/medium/hard 的 B1 初始计划全部通过，导致这些难度没有 repair 机会；75 条 repair 记录全部来自 infeasible 的防御纵深设计。v6 修复层在 easy 上的 50/50 救回能力没有被否定，而是在 v7 中没有触发机会。

## 2. 可行任务：B0 与 B1

### 按难度

| scope / metric | n | v6 | v7 | v6 only | v7 only | both | exact p |
|---|---:|---:|---:|---:|---:|---:|---:|
| B0 easy valid | 50 | 50 | 50 | 0 | 0 | 50 | 1 |
| B0 easy goal | 50 | 50 | 50 | 0 | 0 | 50 | 1 |
| B0 medium valid | 25 | 25 | 25 | 0 | 0 | 25 | 1 |
| B0 medium goal | 25 | 25 | 25 | 0 | 0 | 25 | 1 |
| B0 hard valid | 175 | 175 | 175 | 0 | 0 | 175 | 1 |
| B0 hard goal | 175 | 175 | 175 | 0 | 0 | 175 | 1 |
| B0 feasible valid | 250 | 250 | 250 | 0 | 0 | 250 | 1 |
| B0 feasible goal | 250 | 250 | 250 | 0 | 0 | 250 | 1 |
| B1 easy valid | 50 | 0 | 50 | 0 | 50 | 0 | 1.78e-15 |
| B1 easy goal | 50 | 0 | 50 | 0 | 50 | 0 | 1.78e-15 |
| B1 medium valid | 25 | 25 | 25 | 0 | 0 | 25 | 1 |
| B1 medium goal | 25 | 25 | 25 | 0 | 0 | 25 | 1 |
| B1 hard valid | 175 | 175 | 175 | 0 | 0 | 175 | 1 |
| B1 hard goal | 175 | 175 | 175 | 0 | 0 | 175 | 1 |
| B1 feasible valid | 250 | 200 | 250 | 0 | 50 | 200 | 1.78e-15 |
| B1 feasible goal | 250 | 200 | 250 | 0 | 50 | 200 | 1.78e-15 |

关键点：v7 的改进完全集中在 v6 已知的 easy B1 prompt/输出形态缺陷上；B0 在可行任务上与 v6 完全持平。

## 3. 不可行任务：显式拒绝

| scope | n | v6 refusal | v7 refusal | v7 pbw | exact p |
|---|---:|---:|---:|---:|---:|
| B0 infeasible | 25 | 0 | 25 | 0 | 1.78e-15 |
| B1 infeasible | 25 | 0 | 25 | 0 | 1.78e-15 |
| R0 infeasible | 25 | 0 | 25 | 0 | 1.78e-15 |
| R1 infeasible | 25 | 0 | 25 | 0 | 1.78e-15 |
| R2 infeasible | 25 | 0 | 25 | 0 | 1.78e-15 |

v7 的拒绝原因均指向闭世界可见对象/目标对象缺失，例如 `orange_cube_99`、`gray_cube_99`、`yellow_cube_99` 不在 visible list 中。错误码统一为 `INFEASIBLE_RESPONSE`。

对 R1/R2 的 pass-but-wrong：v6 分别是 5/25 和 4/25；v7 均为 0/25。由于样本量只有 25，这两个下降的 exact McNemar p 分别为 0.0625 和 0.125，单独看不能宣称统计显著；但结合 B0/B1/R0 也全部拒绝，v7 的协议设计在整条链路上消除了这类行为的观测值。

## 4. 修复层

| mode | v6 n | v7 n | v6 valid/goal | v7 valid/goal | v7 pbw | 解释 |
|---|---:|---:|---:|---:|---:|---|
| R0 | 75 | 25 | 0/75, 0/75 | 0/25, 0/25 | 0 | v6 含 easy 失败；v7 easy 无失败，仅 infeasible 触发 |
| R1 | 75 | 25 | 55/75, 50/75 | 0/25, 0/25 | 0 | v6 的 easy 修复成功没有对应触发机会；infeasible 改为拒绝 |
| R2 | 75 | 25 | 54/75, 50/75 | 0/25, 0/25 | 0 | 同上 |

需要特别避免误读：

- v7 的 repair valid/goal 为 0% 不是修复能力退化，而是**没有可救回的可行失败**。
- v7 的 repair 记录数从 225 降到 75，主要原因是 easy B1 初始失败从 50 个 seed-slice 变为 0。
- v7 的 repair 结果全部是显式拒绝，因此这不是“修复成功”，而是“不可行时拒绝执行”的安全行为。

## 5. 对论文叙事的含义

1. v5 → v6 是**歧义消融**：证明场景描述歧义会系统性压制 hard 任务的规划成功率。
2. v6 → v7 是**协议与完整性消融**：修掉 easy B1 的 partial-plan prompt 缺陷，并加入不可行任务的显式拒绝协议和确定性闭世界检查。
3. 论文中的贡献不应写成“结构化方法总体优于直连”。v6/v7 中 B0 与 B1 在可行任务上均为 250/250；结构化链路的价值主要体现在协议化输出、前缀/修复机制、可解释错误层，以及与 validator 的组合方式。
4. v7 的安全结论应保守表述：在当前闭世界、缺对象型 infeasible 任务上，系统能稳定拒绝并消除 pass-but-wrong；这不能自动推广到开放世界或更复杂的不可达目标。
5. 由于 medium/hard 已达 100%，当前这套 55-task 套件对可行性规划部分接近天花板。若继续做算法性对比，应引入更难的可行任务或新的失败源，而不是继续调 prompt。

