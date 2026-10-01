# 持续场景中的有界重新观察

## 机制

`multiobject_pickplace_smoke.py` 新增 `--reobserve-rounds`（0 至 3 次额外观察）和 `--blackout-rounds`（前 N 轮人为缺失反馈）。每轮重新渲染两帧，以 RGB 目标运动和本体感知末端运动检查持物。只有当轮持物证据与目的区域观测支持时，才用现有 Validator/Compiler 下发剩余 place。

观察探测在 x 方向交替移动约 4cm，不累积向上漂移。探测本身也是动作，有步数成本并可能影响物体；不能当作零成本被动观察。达到次数上限则 `stop / observation_budget_exhausted`，停止下发后续动作，但不关闭夹持或声称物理系统进入安全停机。

不重复 pick、不重置场景、不读取真值补齐缺失观测。观测 ID 每轮更新，绑定根据新帧更新。目标颜色、初始参照像素和高度仍是受限固定场景假设。

## 实际小规模检查（seed=0）

| 条件 | 观察轮数 | 结果 | 是否再次 pick |
| --- | --- | --- | --- |
| 第一轮黑屏，允许 1 次额外观察 | 2 | unknown → holding_supported → 校验后 place，独立评分成功 | 否 |
| 持续黑屏，允许 1 次额外观察 | 2 | 两轮 unknown，预算耗尽停止，不 place | 否 |
| pick 预算 20 步超时，允许 1 次额外观察 | 2 | 两轮 unknown，预算耗尽停止，不 place | 否 |
| 第一轮黑屏，不允许额外观察 | 1 | unknown，预算耗尽停止，不 place | 否 |

产物位于 `data/collections/multiobject_reobserve_{temporary,persistent,timeout,disabled}_20261001_v1/`。各次运行只初始化一个场景。前三个有额外观察预算的条件与前一版诊断不是相同探测轨迹，不能直接混合；本轮 disabled 与 temporary 使用相同新协议，第一轮相同，仅额外观察预算不同。

## 结论边界

本轮说明受限执行接口能够在短暂反馈缺失后，通过新观测恢复继续执行；同条件关闭重新观察时没有继续执行。这是一个配对机制演示，不是有统计支持的性能提升。

持续反馈缺失和未抓起条件只证明本次没有触发错误 place，不证明一般误接受率为零。尚未测试自然遮挡、抓取后掉落、动态干扰物或多同色目标。

没有调用 VLM、没有自然语言目标解析、没有 R1/R2 和 ROUTED 路由。该机制应称“有界重新观察与剩余动作继续”，不能称完整大模型规划恢复。也没有在线视觉最终目标检查，完成状态依然由评估器独立评分。

下一步停止扩大颜色接口诊断：先把已有语言目标解析与恢复入口接到同一持续场景，使用新的观测状态生成剩余计划；unknown 应先观察或拒绝，不应以预测前缀终态充当实际状态。明确执行失败后的重试需要新的可执行状态证据，本轮没有解决抓取超时后的自动恢复。

示例：

```bash
PYTHONPATH=. /root/autodl-tmp/.venvs/metaworld-lerobot/bin/python \
  scripts/multiobject_pickplace_smoke.py --visual-follow \
  --blackout-rounds 1 --reobserve-rounds 1 --output-dir data/collections/<新目录>
```

本轮没有后台正式实验；旧产物全部保留，不覆盖。
