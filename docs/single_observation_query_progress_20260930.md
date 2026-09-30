# 单请求补充观测诊断（2026-09-30）

## 协议变化

补充观测由“一个请求回答多个事实”改为每次只询问一个事实：`location:<object>` 或 `hand_state:<arm>`。每个响应必须声明该请求是 `resolved` 或 `unresolved`，且关系中的主体必须与请求一致。程序计算出的缺失事实优先于模型自己的 `needs_observation` 布尔标志，防止模型错误地声称状态完整。

## 实际结果

在同一多视角持续场景、seed=0 上运行 `staged_grounding_multiview_20260930_v4.jsonl`：定位、初次语义解析、两个对象位置补问和一个手状态补问，共 5 次模型调用。

| 请求 | 结果 |
|---|---|
| `location:blue_cylinder` | unresolved |
| `location:yellow_cube` | unresolved |
| `hand_state:right` | resolved，`holding(right, red_cylinder)` |

最终因两个对象位置仍未知而拒绝规划和执行。该结果不是任务成功率，也不是视觉闭环成功证据；它是对补问协议和失败层次的诊断。

## 结论

单请求协议解决了多事实混合回答的问题，并证明了夹爪状态在当前近景下可以被独立识别。但模型仍不能稳定确认两个对象位置。下一步应采用候选区域/检测结果辅助对象位置，而不是继续让 VLM 自由生成框和位置关系；VLM 更适合处理自然语言指代和候选选择。任何候选辅助都必须来自独立在线视觉模块，不能使用 evaluator segmentation。
