# 持续多物体场景：RGB 持物证据与条件放置

## 已实现的实际执行链

初始 RGB 颜色定位 → 标定目标坐标 → 固定 pick → 两帧 RGB 与末端自身位置的共运动证据 → 更新区域绑定 → 条件 place → 独立真值评估。

`ch3/execution/visual_holding.py` 不读取物体真值、分割 ID、奖励。支持已知且唯一的蓝色目标；采用已知颜色阈值，缺失、不一致或不足的证据返回 `unknown`。这不是通用物体识别或接触检测。

末端像素由相机标定与机器人自身位置投影获得，因此应称为 **RGB + 本体感知的受限持物证据**，不是纯图像状态估计。

当前阈值为 480px 场景中的启发式：初始到探测末帧目标位移至少 8px，探测两帧中末端与目标运动各至少 4px，相对偏移漂移不超过 6px，目标距投影末端不超过 25px。阈值不是经独立数据集验证的普适参数。只检查两帧，不能排除所有偶然共运动。

## 本次实际测试

### 正例（v2，seed=0）

目录：`data/collections/multiobject_visual_follow_20261001_v2/`

- pick 95 步，独立评估抬升 0.111961m。
- 保持夹爪闭合，用当前位置加固定小位移做探测，19 步。
- 目标相对初始位移 21.38px，探测中目标运动 5.10px、末端运动 4.70px，相对偏移漂移 0.68px。
- 在线证据结果 `holding_supported`，触发 place；place 103 步，释放后额外稳定 30 步。
- 独立评估：蓝色方块完整水平投影位于绿色区域，最终中心高度满足预置范围，`task_success=true`；执行后图像与此一致。
- 场景只初始化一次，没有 pick/place 间重置，模型调用 0。

### 未抓取负例（v1，seed=0）

目录：`data/collections/multiobject_visual_follow_negative_20261001_v1/`

跳过抓取，夹爪打开并做同类探测。目标像素位移为 0，末端运动 4.40px。虽然末端距目标不到 25px，共运动证据不足，返回 `unknown`，**不执行 place**。独立评估未抬升、未完成任务。

这是一个未抓取控制，不是所有失败抓取、遮挡、物体掉落的综合验证。一个正例与一个负例不能支持成功率或误接受率统计。

### 历史记录

`multiobject_visual_follow_20261001_v1/` 保留首次正例运行，已成功放置。但 `blue_lifted` 字段当时在放置完成后计算，不能用它评价抓取阶段；v2 改为独立记录抓取后抬升与最终高度，并收紧评分为整个方块投影位于区域。不得把 v1、v2 当独立样本合并。

## 信任边界与局限

- 放置门控只依赖 RGB 共运动证据；物体真值仅用于评分，不能用于决定是否继续。
- 放置目标由探测后的 RGB 重新定位绿色区域，使用固定平面高度假设。
- 持物对象绑定使用已确认共运动的视觉证据和末端自身位置近似，不是精确物体位姿估计；place 控制器实际使用目的区域绑定进行移动。
- 区域评分使用独立真值与预置几何尺寸，只在释放和稳定后执行；目前没有在线视觉最终目标检查器。
- 尚未接入自然语言解析、Validator/ROUTED、重试或动态恢复，因此不能称为完整规划恢复闭环，更不能证明 ROUTED 优于基线。
- 不涉及 ACT/AD-Flow，仍然是可替换固定控制器后端。

## 下一步

1. 将证据输出适配为规划状态的三值观测：supported / unknown，不把 unknown 转成 hand_empty 或 holding。
2. 补抓取超时、遮挡及抓取后掉落的小规模检查；规划模块只收到在线观测，不收到评分真值。
3. 接入自然语言目标与现有校验、恢复模块，在同一个持续场景内执行剩余计划，并保留所有失败样本。
4. 冻结受限场景与反馈协议后再做配对方法比较；暂不直接扩为正式大规模实验。

## 复现

```bash
cd /root/autodl-tmp/ch3-planning
PYTHONPATH=. /root/autodl-tmp/.venvs/metaworld-lerobot/bin/python \
  scripts/multiobject_pickplace_smoke.py --visual-follow --output-dir data/collections/<新正例目录>
PYTHONPATH=. /root/autodl-tmp/.venvs/metaworld-lerobot/bin/python \
  scripts/multiobject_pickplace_smoke.py --visual-follow --skip-pick --output-dir data/collections/<新负例目录>
```

本轮相关自动测试：62 项通过。无后台正式实验。
