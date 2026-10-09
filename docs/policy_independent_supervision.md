# 模型无关任务监督层 V1

本次范围是大模型任务规划、执行反馈校验与剩余任务修复。代码位于
`ch3/supervision/`，不修改旧实验的 Validator、Goal Checker 或修复协议。
不需要下载策略权重，不训练 ACT、SmolVLA、π₀.₅ 或 AD-Flow。

## 已实现的闭环

自然语言指令与已落地的目标 → 当前观测 → 模型生成剩余计划 →
严格结构检查 → 四层 Validator → 预测目标检查 → 编译与执行一个技能 →
新观测 → 重新确认进度、校验剩余计划或请求修复。

`Task` 保留原始指令、结构化目标和禁止操作对象。
`Observation` 提供 episode、递增序号、观测符号状态、显式手臂占用、目标证据和多相机图片路径。
`Evidence` 的取值为 true / false / unknown，包含来源与解释。
缺失证据与 unknown 都不会被算作完成；新观测替换上一帧证据，允许撤销已完成目标。
证据必须与当前状态一致。指令、动作下发、执行器 success 回执和预测状态均不能直接确认目标。

当前保守要求：调用规划器前，所有注册手臂必须有明确占用观测，所有注册对象必须有位置或持物关系。
缺失观测返回 `need_observation`，由调用者补充观测；本模块不自动伪造空手或对象位置。
目标完成以显式观测证据为准，尚未达到物理世界中的感知可靠性保证。
证据来源标签是接口契约，不是防止调用者伪造证据的安全认证。

每个计划与观测序号绑定。新帧之后必须重新校验才能继续下发。
已下发历史仅作上下文，模型不能改写；任何当前 holding 都来自观测。
允许后缀第一步 place，完成历史 pick；抓取失败则可以重新 pick。
模型候选上限 8 步，拒绝重复 JSON 键、额外字段、未知对象、禁止操作对象和类型强制转换。
仅合法但预测目标不完整的计划不能执行。后续计划检查全部目标，避免故意破坏已完成目标。
每次执行必须等待新观测；预算限制模型调用和命令数量。后端异常当作 unknown 回执处理，不自动重发。

## 接入方式

```python
from ch3.supervision import ClientPlanner, Supervisor, Task
from ch3.vlm.client import DashScopeVLMClient

planner = ClientPlanner(DashScopeVLMClient(model="qwen-vl-plus"))
supervisor = Supervisor(Task(instruction, tuple(goal_facts)), validator, planner)

# observer/backend 由实际环境适配器实现
supervisor.observe(observer.read())
if supervisor.prepare() == "ready":
    supervisor.execute_next(backend)
# 无论 receipt 如何，下一次必须先获得新的 observation。
```

API key 使用现有客户端环境配置，不写入代码或日志。`ClientPlanner.usage` 保留调用成本元数据。
观测图片作为额外输入，当前模块不负责从图像中识别对象或自动解析目标。
自然语言到结构化目标的落地与视觉状态提取仍需前端适配；不得把评分真值输入监督层。

执行适配器签名：
`execute(step, *, instruction, context, command_id) -> Receipt`。
`step` 是经过检查的语义动作编译结果，`instruction` 始终保留任务原始指令；
`context` 包含剩余目标、当前观测与不可改写历史。
底层策略接收的实际输入由适配器决定，不假定 π₀.₅ 支持技能子指令。
策略名称只来自注册表；旧注册表中的 adflow_* 名称不表示本版本已接入 AD-Flow。
后端应将 command_id 用于幂等记录，控制调用超时，并验证自身支持的动作空间。
本监督层同步调用后端，不负责取消卡死的网络/控制进程，也不执行力控或碰撞检查。

## 验证与证据边界

```bash
cd /root/autodl-tmp/ch3-planning
PYTHONPATH=. /root/autodl-tmp/.venvs/metaworld-lerobot/bin/python -m pytest -q
/root/autodl-tmp/.venvs/metaworld-lerobot/bin/python scripts/supervision_smoke.py
```

演示使用脚本化模型、符号执行器与 mock observer。第一步抓取失败，模型被重新调用，
实际下发序列为 pick（失败）→ pick → place，最后由新观测确认完成。
结果只能证明接口与软件闭环连通，不是 Qwen 推理效果、视觉感知精度或机器人恢复成功率。

2026-10-08 本地验收：新增监督层测试 19 项通过，全项目测试 383 项通过。
演示为 2 次脚本化模型调用、3 次命令下发，最终状态 complete。
默认 conda Python 未安装 pytest；使用上述现有虚拟环境和 PYTHONPATH，
确保 CLI 子进程也能正确导入项目。不需要额外安装依赖或下载权重。

后续只需选定可用控制后端、实现 observer/backend 适配器并运行小规模接入验证。
确认模型真实调用、观测独立于评分、修复计划改变执行序列之后，才能冻结正式实验协议。
底层模型尚未确定，因此 RoboDojo 的策略推理、真实动力学执行和非结构化场景评测均未完成。

## 2026-10-09：统一会话入口与审计日志

新增 `ch3/supervision/runtime.py` 与 `scripts/run_supervision.py`。
统一入口支持可信本地 factory 创建 `Session(supervisor, observer, backend, evidence_kind)`，
后续只替换 observer/backend 和模型配置，不重写运行循环。
factory 是可执行 Python 插件，只应使用作者信任的本地模块。

默认明确使用 mock，不读取 API key、不调用收费接口：

```bash
cd /root/autodl-tmp/ch3-planning
/root/autodl-tmp/.venvs/metaworld-lerobot/bin/python scripts/run_supervision.py \
  --max-observations 20 \
  --output /tmp/supervision_mock_$(date +%Y%m%d_%H%M%S).jsonl
```

日志逐行刷新，包含观测证据、模型候选与拒绝原因、执行动作、command_id、回执和最终摘要；
文件以独占模式创建，拒绝覆盖旧结果。接入 `ClientPlanner` 时摘要还包含模型返回的 token 与耗时。
API 失败计入监督层调用预算，但没有服务端 usage 时不能凭空估算 token 消耗。
日志包含任务、图片路径和模型输出，分享前应按数据隐私要求检查。

观察序号重复、episode 改变或观测器异常立即停止；观测预算耗尽不算成功。
unknown 执行回执之后必须重新观测，不能盲目重发。
状态已经显示目标满足但缺少明确证据时，继续请求观测，不生成多余动作。
会话的 `evidence_kind` 由集成方如实声明，不是软件自动认证的实验类型。
只有最终 `stop_reason=complete` 才表示本会话根据给定观测证据确认完成，
不能把 mock/symbolic 标签结果写作真实机器人成功率。

运行入口仍是同步接口，观察/控制适配器必须自行设置 IO 超时与资源清理；
本实现不提供断电后的自动恢复或多进程并发调度。

2026-10-09 本地验收：监督层与统一运行入口相关测试共 32 项通过，全项目
396 项测试通过。实际运行默认 mock 会话得到 4 次观测、2 次脚本化模型调用、
3 次命令下发，最终由执行后的独立新观测确认完成。
另通过替身网络响应验证现有 Qwen 客户端贯穿首次规划与失败修复流程，
没有调用真实服务；这些结果仅用于软件集成验收，不构成模型性能实验。
