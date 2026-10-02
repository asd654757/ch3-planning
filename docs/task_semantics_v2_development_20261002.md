# 任务语义 v2 开发修正

针对v1保留的12条开发指令：

- 将输出改为紧凑填写式contract+evidence，不把完整Pydantic JSON schema塞入提示。
- 明确可用目的包含table；“放回桌面”必须生成位置目标，不仅hand_empty。
- 历史旧目的只作历史，不能用作新请求的缺省值。
- 明确缺失对象的整任务拒绝、无定指代的澄清；拒绝不得授权部分目标。
- 每个目标、禁止、顺序、空手与status附原指令精确片段。程序检查字段覆盖、非空、片段确实存在；**不保证片段能蕴含该语义，也不解决遗漏未输出约束**。
- 规划提示增加当前持物依赖与临时释放说明，其中包含本固定候选状态的黄先蓝后五步策略提示。这是开发prompt补强，不应包装成新搜索算法或独立泛化结果。

仍为显式状态、已知颜色候选、冻结单帧的符号诊断，不是在线视觉闭环或开放世界。原v1结果保持不变，v2在独立目录全部记录。

通过也只允许进入下一关：冻结未调过的新措辞/组合，与透明自然语言规则解析基线及模型解析+确定性搜索比较；不能从这12条的复测宣称LLM必要性。当前代码尚未将语义契约接入物理控制器，所有许可标记physical_execution_authorized=false。

## v2结果与v3有界诊断

v2目录`data/collections/task_semantics_pilot_v2_20261002_190630`12/12完成，全部在schema或证据覆盖处拒绝，无物理执行。不能把零误接受解释成语义成功或充分安全：这是全拒绝。原始输出仍能看到漏返回目标与orange→yellow替代。

v3将quote嵌入各goal/forbidden对象，取消ready必填status证据的冗余要求，拒绝仍须reason_quote。提示不提供旧目的，保留已执行blue pick与当前持物事实。**语义解析调用不输入图像**，只消费共享已落地候选和状态；规划调用仍用同一冻结图像。这是前端分解开发试验，不能把收益归因于视觉，也不能声称隔离了单一变量（schema、prompt与输入模态同时变化）。仍不声称证据片段能验证语义真实性。

本轮只限再跑12条开发指令，不自动扩规模。新增可复用分析脚本保留所有预期case分母，单列缺测、schema拒绝、正确澄清与gold计划成功。

## v3结果与JSON输出模式诊断

v3目录`data/collections/task_semantics_pilot_v3_20261002_191157`12/12完成，严格语义与gold计划均2/12（可执行任务2/10）；其余多为生成重复键、无关额外字段、JSON截断，另有quote来自提示规则而非指令。JSON模式下模型先填出合理字段，再不停添加无关字段或重复hand_quote，直到1024token截断。不能将该现象直接等同模型理解失败，也不应提高token预算掩盖非终止输出。

v4为有界配对开发诊断：**保持v3语言解析prompt、输入、温度、seed与token上限，只关闭API response_format=json_object**。严格JSON/schema/quote检查不放松、不自动截取字段、不用gold纠正答案。新增finish_reason、prompt/completion token日志帮助区分长度截断与语义错误。该诊断不是正式模型比较，v3/v4运行时点不同亦不能保证服务端完全无漂移。

## v4输出模式与v5纯外包装解码

v4目录`data/collections/task_semantics_pilot_v4_20261002_191806`12条生成均finish_reason=stop，不再重复扩写；12条均带完整JSON Markdown fence，原严格JSON解码全部拒绝。不能把此全拒绝当成全部语义错误。

对**冻结v4原文**仅做解码回放（`offline_fence_replay.json`，0次调用）：去掉单个完整外层fence后10条通过schema/quote并精确匹配gold；order_2目标证据拼接而非原指令连续片段、ambiguous_1使用提示规则作reason_quote且残留hand_quote，仍拒绝。回放只用于定位格式问题，不是实际端到端模型执行结果。

v5沿用v4提示与非JSON输出模式；唯一输出处理变化为完整单JSON fence规范化。拒绝说明文字、多个fence、残缺JSON、重复键、NaN/Infinity、额外schema字段；不截取合法前缀、不删除不合法语义、不使用gold补全。v4原记录保持不改写，v5独立全量重跑12条开发任务。模型规划阶段保持原live调用与独立gold评分，无新的物理执行。
