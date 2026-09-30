# 持续场景多视角观测诊断

## 已实现

- 同一次 reset、30 步静置后，采集 corner2、topview 和固定夹爪近景；采集过程中检查 qpos 和仿真时间不变。
- 近景是固定自由相机，并非装在手腕上的相机；参数保存在 summary 中。
- 图像翻转与 segmentation 翻转一致；segmentation 和真值框仅存 evaluation_only。
- 隐藏原生 goal site 的显示但保留 site 本身，避免破坏 native reset。XML include 展开可能产生多个 worldbody，必须遍历整个 XML 的 sites。
- 定位仅输入原始 corner2，语义阶段可输入同状态多视角拼图，防止跨视角重复定位及坐标混淆。记录两张输入各自的路径与哈希。

## 版本 lineage

- multiobject_multiview_smoke v1：gripper_view 并非原生相机，采集失败；不是有效样本。
- v2：相机采集完成，但只遍历首个 worldbody，没有真正隐藏目标标记；summary 的隐藏标记意图不能当作视觉验证结果。
- v3：修复遍历范围，目视确认蓝色悬浮目标球消失，夹爪可见；用于实际模型调用。
- v4：增加每视角 evaluator 真值框与自由相机参数记录，没有新增模型调用。v3/v4 的 corner2 与 multiview PNG 用 cmp 验证字节完全一致，因此独立诊断可用 v4 的 corner2 真值框评分 v3 图像预测。

## 实际调用与结果

`staged_grounding_multiview_20260930_v1.jsonl`：seed=0，2 次模型调用，无执行。

- 预测 4 个实体，目标解析为 on(red_cylinder, green_square)。
- 蓝色对象仍误描述成 cylinder；几何匹配 IoU：红 0.257、蓝 0.084、黄 0.649、绿 0.536。只有 2/4 达到 0.5。
- 语义只输出红/绿的位置，遗漏蓝/黄及手状态；needs_observation=true，但没有具体 uncertainty 描述。
- 因观测不足被拒绝，未调用规划或机器人操作。

这不是视觉性能改善的正式证据。清理标记后预测实体数与真值数相等，也不意味着实体身份、框或状态正确。模型自报 confidence=1.0 同样不能作为可信几何保证。

## 下一步优先项

1. 将语义任务拆成逐实体/逐手的状态查询，缺失项必须返回显式 unknown 和原因；以未覆盖实体/手状态驱动观测请求，而不是全量答案反复重试。
2. 将精确定位从自由生成框中解耦：评估独立视觉检测/分割或候选区域提取，VLM 负责候选指代和自然语言目标解释。不得用 evaluator segmentation 作在线候选。
3. 独立评估实体匹配和手状态，加入错误状态拒绝测试；确定绑定协议后才开始实际多物体操作。

当前仅有补充观测采集能力；尚未实现自主相机选择、实体跟踪、视觉对象执行绑定或自动补充观测恢复闭环。不能宣称这些已完成。
