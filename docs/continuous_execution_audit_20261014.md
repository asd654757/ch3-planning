# 实际连续执行链路审计（2026-10-14）

## 范围与结论

本次只读核实代码和现存物理执行记录；没有新调用 API，没有启动新实验。
审计数据：`data/collections/flexible_v8_formal_20261004_050419/{medium,hard}`。

已有真实模型参与的持续物理执行链路，但它是 `FlexibleFixturePlanner` 的
`execution_contract_v8`，不是最新 `ContractRecovery` pilot 的物理实现。
最新 `scripts/contract_recovery_pilot.py` 明确为 `symbolic_only`，不应与物理结果混报。

## 记录核对

| 项目 | medium | hard |
|---|---:|---:|
| episode 数 | 60 | 60 |
| 报告单次初始 reset | 60 | 60 |
| 检出 RGB 目标偏离 | 60 | 60 |
| 实际恢复模型调用 | 60 | 60 |
| 有记录的恢复后缀 | 60 | 60 |
| 物理技能执行事件 | 358 | 474 |
| 独立终态评分完成 | 58 | 58 |
| 成功 | 58 | 58 |
| 模型输入图像哈希匹配落盘 PNG | 120/120 | 120/120 |
| 每次 release 事件对应探测帧存在 | 178/178 | 236/236 |
| 被拒模型候选 | 0 | 0 |

两方法在每个条件下均 29/30，没有性能差异。medium 的两个失败为
`holding_not_supported`；hard 的两个失败为
`destination_observation_budget_exhausted`。
每个条件有 30 个初始候选共享实例：共享的是初始输出，不是恢复输出；
因此 120 条审计请求不等于 120 次 API 调用，每个条件实际调用 90 次。

## 代码链路

1. `make_scene()` 创建一个环境、执行一次显式 `env.reset()`；
   `run()` 的 transfer 循环不再重建或重置环境。报告中的 `episode_resets=1`
   是代码赋值，不是 reset 拦截器计数，不能当作独立运行时证书。
2. 初始模型候选通过 `evaluate_flexible()` 校验和编译。
   flexible 分支用 `sequence_from_steps(executable.steps)` 形成执行顺序，
   不是直接执行固定参考序列。
3. 同一个 `FixedPickPlaceController` 在同一个 env 中执行连续 pick/place。
4. 执行后重新渲染 RGB；holding、release 和 observed goal 分开判定。
5. release 已支持但 observed goal 不满足时，调用
   `released_object_repair()`；该路径标记为 `execution_repair`，并真正调用模型。
6. 返回候选再次经过 Validator、目标覆盖、保护对象、区域占位及剩余预算校验。
   接受后替换完整未执行后缀，再在同一环境执行。
7. 每次 transfer 前更新 `attempted_transfers=n+1`；失败的物理尝试仍占预算。

## 证据边界与缺口

- 物体/目标区域 body pose 在此 flexible 分支用于终态评分，不传给规划器。
  但系统使用已知相机标定、平面高度及末端位置反馈；不能称为完全无特权或
  纯图像端到端系统。
- region reference 是接触前 RGB 得到的固定参考，后续不是每次重新估计其坐标。
- 目标、对象注册表及初始 table 状态由受控 fixture 提供；不是开放世界任务解析。
- 实验为受控 `.045m` 放置目标偏移；hard 另有预注册目标交换，不能称自然失败。
- 没有被拒候选实例，因此只能通过代码和测试验证拒绝门控，不能从这批物理结果
  证明“非法候选确实被阻止执行”。
- flexible 的保护对象主要是禁止后续计划操作，没有持续物理位移证书；
  `protected_object_preserved` 的默认真值不能用来证明物理保护成功。
- 模型响应审计没有完整保存 model/base URL/response ID；物理入口默认
  `qwen-vl-plus`，不能仅凭这些报告认定为 DeepSeek。
- 最新 `ContractRecovery` 未接入这些物理入口；当前无法声称其已完成连续物理验证。

## 下一步门槛

先接入明确可配置模型及响应 provenance，并在连续入口加 reset、frame、
candidate acceptance、dispatch、suffix replacement 的运行时事件关联。
随后做少量真实模型物理审计，检验拒绝候选不执行和恢复后缀确实执行。
门槛通过后再决定正式样本量，不因旧结果饱和而直接扩大实验。

相关单元测试：`test_flexible_fixture_planner.py` 和
`test_contract_recovery.py`，本次运行 23 passed。单元测试不等于新增物理实验。
