# 观测驱动的剩余动作校验与失败门控（2026-10-01）

## 本次改变

`observed_continuation.py` 将受限 RGB/本体感知持物证据适配到现有 Validator 和 Compiler。观测支持 holding 且目的区域可见时，建立仅包含蓝色方块和绿色区域的局部状态，校验剩余 place，再编译为固定控制器请求。

- 剩余计划采用局部编号 1 校验；实际下发步骤仍为全局步骤 2。
- 后端能力注册表为 `fixed_pick`/`fixed_place`，不使用 AD-Flow 名称冒充实际策略。
- `unknown` 直接产生 `observe_again` 决策，不构造空手状态，不生成可执行 place，不调用 Validator 用默认空手假设补齐观测。
- 绿色区域位于桌面的事实是该受限场景的**建模假设**，单独列入 `fixture_assumptions`；不能称为视觉估计事实。
- `predicted_goal_satisfied` 是剩余计划模拟后的符号目标检查，不是实际任务完成；实际结果仍由独立评估器评分。

## 小规模实际仿真检查

三个条件均使用 seed=0，每次在独立初始化的场景中测试；单次场景内 pick、探测、place 不重置。不存在统计意义上的三种方法比较。

| 条件 | 实际抓取 | 在线证据 | 剩余计划处理 | 放置 | 独立任务评分 |
| --- | --- | --- | --- | --- | --- |
| 正常 | 抬升约 11.2cm | holding_supported | Validator 通过，预测目标满足，Compiler 编译 | 执行 | 成功 |
| 抓取预算 20 步 | 超时，未抓起 | unknown，目标未随末端移动 | observe_again，不构造可执行后缀 | 未执行 | 未完成 |
| 反馈全帧黑屏 | 实际抓起 | unknown，目标像素缺失 | observe_again，不构造可执行后缀 | 未执行 | 未完成 |

记录位于：

- `data/collections/multiobject_observed_continuation_positive_20261001_v2/`
- `data/collections/multiobject_observed_continuation_timeout_20261001_v2/`
- `data/collections/multiobject_observed_continuation_blackout_20261001_v2/`

`feedback_*.png` 是送入颜色证据模块的实际帧；`probe_*.png` 是同一时刻未处理的场景图像，保存用于诊断，不用于绕过黑屏输入。全帧黑屏是人工反馈缺失，不是自然遮挡实验。

v1 保存第一轮运行。当时日志将绿色区域的桌面位置混列入观测事实；后续仅修正事实来源标注，并在 v2 重新运行，区分 `observed_facts` 与 `fixture_assumptions`。不覆盖旧记录，不合并重复运行作为样本。

## 能与不能证明的内容

能证明这个局部执行路径已经使用观测门控和现有校验器，而不是单凭控制器 completed 推进成功状态；两个受控失败条件没有触发错误放置。

不能证明 ROUTED 恢复率提高、开放世界泛化或完整自然语言视觉闭环。这里没有 VLM 调用，没有 R1/R2，也没有已实现的自动重新观察/重试；`observe_again` 目前是决策出口，运行在该处结束。黑屏条件下成功抓起但暂停，属于拒绝不确定执行，不是成功恢复。

局部状态只含当前 place 的相关实体，不包含未观测干扰物状态；不等于完整场景建图、运动规划或碰撞安全验证。

## 下一步应优先做什么

1. 接通 `observe_again` 的真实重新采样与有界重试，区分观测不足与明确技能失败，不盲目重复 pick。
2. 接入已有自然语言/图像目标解析与 R1/R2 恢复入口；发生失败后从当前新观测生成剩余任务，不重新执行旧前缀。
3. 先冻结少量连续场景任务与自然/受控失败协议，再比较不恢复、状态反馈恢复和视觉反馈恢复；不继续扩大单纯颜色抓取测试。

复现条件示例：

```bash
PYTHONPATH=. /root/autodl-tmp/.venvs/metaworld-lerobot/bin/python \
  scripts/multiobject_pickplace_smoke.py --visual-follow --pick-max-steps 20 \
  --output-dir data/collections/<新目录>
```

将 `--pick-max-steps 20` 换成 `--occlude-feedback` 为人工反馈缺失条件；两者都省略为正常条件。输出目录必须不存在。
