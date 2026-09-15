# State Visibility Pilot v1/v2 分析报告

生成时间：2026-09-15  
模型：`qwen3-vl-flash`  
修复模式：`R1_FROM_STATE`  
执行协议：冻结扰动失败点 → 状态/视觉反馈修复计划 → MetaWorld 执行  
manifest：`data/collections/state_visibility_pilot_manifest_20260915.json`

## 1. 实验定位

本实验回答两个问题：

1. 修复阶段需要多少执行状态信息；
2. 失败帧作为视觉提示能否在稀疏状态条件下提供补偿。

三臂定义如下：

| 实验臂 | 反馈内容 |
|---|---|
| `FULL_STATE` | 完整结构化执行后状态、持物/transport 派生状态 |
| `SPARSE_STATE` | 指令、闭世界对象表、目标事实；不给执行后状态 |
| `SPARSE_STATE_VISUAL` | 稀疏状态 + 失败帧 advisory 输入 |

注意：该实验不是 vision-only 状态估计闭环，视觉帧只作为附加提示，结构化符号反馈仍然存在。

## 2. v1 到 v2 的协议修正

### v1 结果

数据：`data/collections/state_visibility_pilot_20260915_095726.jsonl`

| 实验臂 | Symbolic Valid | Schema Error | Final Success |
|---|---:|---:|---:|
| `FULL_STATE` | 30/30 | 0 | 28/30，93.3% |
| `SPARSE_STATE` | 27/30 | 3 | 26/30，86.7% |
| `SPARSE_STATE_VISUAL` | 15/30 | 15 | 15/30，50.0% |

v1 的 15 个视觉臂 schema error 均集中在 `pick_place`，表现为模型倾向只输出 `pick` 而缺少对应的 `place`，形成 dangling-pick 结构错误。

诊断结论：v1 不能证明视觉输入无效，因为稀疏反馈缺少目标动作骨架；失败帧进一步强化了“立即抓取”的局部偏置。这属于 prompt/协议缺陷，而不是可靠的视觉能力结论。

### v2 修正

v2 在稀疏状态臂中加入 `goal_action_skeleton`：

- 骨架由目标事实程序化推导；
- 只描述完成目标需要的动作模式，如 `pick + place`、`push`、`press`；
- 不包含当前执行状态、前缀终态、持物状态或 required transport；
- 保持 `FULL_STATE` 与稀疏臂之间的反馈完整度差异。

修改点：

- `ch3/vlm/repair.py`
- `scripts/state_visibility_pilot.py`
- `scripts/launch_state_visibility_pilot.sh`
- `tests/test_repair_state_visibility.py`

## 3. v2 正式结果

数据：`data/collections/state_visibility_pilot_20260915_101428.jsonl`  
规模：30 点 × 3 臂；`FULL_STATE` 复用冻结参照，其余两臂重新调用模型，共 60 次 VLM 调用。

### 3.1 总体结果

| 实验臂 | Symbolic Valid | Schema Error | Sim Attempted | Final Success | Prompt Tokens |
|---|---:|---:|---:|---:|---:|
| `FULL_STATE` | 30/30，100% | 0 | 30 | 28/30，93.3% | 34,803 |
| `SPARSE_STATE` | 30/30，100% | 0 | 30 | 29/30，96.7% | 28,100 |
| `SPARSE_STATE_VISUAL` | 30/30，100% | 0 | 30 | 28/30，93.3% | 36,710 |

### 3.2 分技能结果

| Family | `FULL_STATE` | `SPARSE_STATE` | `SPARSE_STATE_VISUAL` |
|---|---:|---:|---:|
| `pick_place` | 13/15，86.7% | 14/15，93.3% | 13/15，86.7% |
| `push` | 8/8，100% | 8/8，100% | 8/8，100% |
| `press` | 7/7，100% | 7/7，100% | 7/7，100% |

### 3.3 配对结果

三臂全部成功的点数为 26/30。其余 4 个失败点互不相同：

| 配对模式 | 点数 |
|---|---:|
| 三臂全部失败 | 1 |
| 仅 `FULL_STATE` 失败 | 1 |
| 仅 `SPARSE_STATE` 失败 | 1 |
| 仅 `SPARSE_STATE_VISUAL` 失败 | 1 |

由于样本量只有 30 点，差异不构成显著结论；不应在正文中声称稀疏状态优于完整状态。

## 4. 可写入论文的结论

1. **目标动作骨架解决了 v1 的结构崩溃。**  
   `SPARSE_STATE_VISUAL` 的 schema error 从 15 降到 0，`SPARSE_STATE` 从 3 降到 0，说明问题来源于协议缺少动作补全约束，而不是视觉输入本身。

2. **稀疏状态 + 目标骨架足够完成当前修复任务。**  
   在 v2 中稀疏状态臂达到 29/30，说明完整执行状态不是唯一可行的反馈形式。

3. **失败帧当前没有显示额外收益。**  
   在已有稀疏符号反馈和目标骨架的条件下，加入失败帧没有提高修复成功率，还带来约 28.7% 的额外 prompt token 开销。  
   诚实的结论是：在当前任务、模型和反馈协议下，视觉帧未提供超越目标骨架的补偿价值。

4. **该负结果具有方法学价值。**  
   v1 → v2 的变化说明：视觉反馈评估必须与反馈表示、动作骨架和 prompt 结构一起设计，否则容易把协议缺陷误判为视觉能力缺陷。

## 5. 论文写作口径

建议将本实验命名为“反馈信息完整度消融”或“状态可见性消融”，而不是“视觉闭环实验”。

推荐表述：

> 在 30 个受控扰动点上，稀疏状态反馈配合目标动作骨架可以达到与完整状态反馈接近的修复成功率；加入失败帧后未见额外性能增益，但也不会破坏结构化输出。v1 中出现的 schema 崩溃主要由缺少目标动作骨架引起，修正后所有实验臂的符号层有效性和 schema 错误均恢复一致。

不应写成：

- 视觉反馈无效；
- 视觉闭环失败；
- 稀疏状态优于完整状态；
- vision-only 状态估计可以替代结构化状态反馈。

## 6. 后续工作建议

按优先级：

1. 将 `Self-Refine` / `Checker-loop` 等公平基线也接入同等 prompt v2、goal skeleton 和状态反馈协议；
2. 增加 State Visibility 样本量，尤其是 `pick_place`，确认稀疏与完整状态差异；
3. 补充失败帧视觉内容分析，例如失败帧是否暴露目标偏移、遮挡或执行中断；
4. 在更难或长程任务中重新评估视觉反馈，因为在当前短程多技能任务中目标骨架已基本决定恢复动作。
