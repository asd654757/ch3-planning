# 规则自然语言解析对照 v1（冻结新集之前）

`ch3/vlm/rule_task_semantics.py`不访问case_id、gold或特定指令答案。共享模型的blue/yellow/green候选、table目的与持blue显式fixture。不输入图像，因为模型语言解析也不输入图像；两者均消费相同已落地状态。

支持的受控语法（大小写不敏感，分号/句号/then/and分句）：
- 英文put/place/return/take/move/carry + blue/yellow/held cube + to/on/onto/in + table/green；back可选。
- 中文蓝色方块/蓝块/黄色方块/黄块/手里那个/手上的方块 + 放回/放到/送到/搬到 + 桌面/绿色区域。
- do not touch/move/pick or place，leave X untouched；不要碰/不要移动/禁止移动。
- put/place/move both cubes in green；两个方块都放到绿色区域。
- X first Y second；先放X再放Y；至少两个目标的先后顺序。
- finish/end empty-handed / with an empty hand；最后空手/然后结束。
- cancel/forget old/previous/blue-to-green task/goal/instruction；取消旧任务/取消之前的目标。

任一不可识别分句使整任务clarify，不抽取已认识的一部分并丢掉约束；目录外颜色物体和已知不支持技能使unsupported。冲突目标澄清。语法规则覆盖多个任务家族，不故意只保留旧运输模板；仍然是受控语言基线，不是完整自然语言系统。

公平边界：新集由作者创建，作者知道该语法；不能声称独立第三方测试。需要分别报受控语法/自由改写/应拒绝子集，避免只选择同义词超出语法的句子制造模型优势。规则的clarify在可执行gold上是覆盖失败，但安全停止不同于危险误执行。

本次冻结先只扩措辞与组合，当前状态仍固定持blue；不同持物/空手状态须后续独立扩展，不能用本集证明多状态泛化。也无物理执行或新视觉状态估计。
