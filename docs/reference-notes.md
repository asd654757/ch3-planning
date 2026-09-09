# 参考代码精读笔记（2026-09-08）

精读对象：`/root/autodl-tmp/ch3-references/` 下四个开源项目。目的：为阶段 0/1 骨架的接口分层、schema、状态推进、execution feedback 提供依据；**只借鉴结构，不复现**。

## 1. PragmaBot（ETH，RA-L 2026）— schema + 成功检测

路径：`pragmabot/pragmabot/src/pragmabot/`

要点：
- 所有 VLM 结构化输出都用 **pydantic BaseModel** 定义，如 `NextBestAction`、`SceneDescription`、`SuccessEvaluation`；`query_structured()` 让 VLM 直接返回 JSON 并解析成对象（解析失败抛 ValueError）。→ 我们 ModelPlan 用 pydantic + 生成 JSON Schema 冻结。
- `RobotSkill(str, Enum)`：技能用枚举闭合（PUSH/PICK/PLACE）；动作参数按技能**条件必填**（place 才有 placement_object；pick 才有 grasp section），用 `Optional[bool] + Field(description=...)` 约束。→ 我们 pick/place 参数规则类似，但参数完备性由 capability 校验强制，不依赖 prompt。
- `SuccessEvaluation`：**区分 is_action_successful 与 is_task_completed** —— 动作级 vs 任务级。→ 直接对应我们的 per-step Execution Feedback 与 goal ⊆ S_T 两级判定。
- `VLMSuccessDetector`：before/after 两张图 + 规则 prompt，判断动作/任务成功。**评审决定我们不新增 VLM detector**，而是做成 `ExecutionFeedback`（SUCCESS/FAILED/UNKNOWN）+ 可插拔 provider（夹爪/位姿/AD-Flow 返回/简单视觉）。保留其"动作级 vs 任务级"二分思想。

## 2. AutoTAMP（ICRA 2024）— checker 开关式 ablation

路径：`autotamp/`

要点：
- `autotamp_single_agent.py` 顶部 `syntactic_correct_loop / semantic_correct_loop` 布尔开关，直接决定保存路径与是否进入检查循环；README 说明可开/关 checker 做 ablation。→ 我们实验 A 的 B1/B2a/B2b 本质是同一 P 上"不开/开校验/开校验+修正"的开关式消融，结构上同源。
- `openai_func.py`：`GPT_response_first_round/second_round` 两轮调用对应它的"发现错误→反馈→再生成"；`check_syntactic_correct` 等是确定性解析后检查。→ 对应我们的 R1（全量重生成）与 R2（前缀保留修正）都基于 first_invalid_step + error message + current state 反馈。
- 不需要 Gurobi/TAMP：只取"checker 如何组织 + 开关消融 + 两轮调用反馈"。

## 3. ROS-LLM（Huawei Noah，Nature MI 2026）— LLM 与执行解耦

路径：`hebo/ROSLLM/`

要点：
- `agent_comm/scripts/llm_node`：LLM 调用被包装成独立 service，带 model/temperature/timeout，异常捕获为 `success=False + info`，LLM 失败不会拖垮下游。→ 我们 planner/repair 调用也应有超时/失败/结构化解析失败的状态返回，并记入日志。
- `rosllm_msgs/msg/LLMResponse.msg`：`success / info / response / req_time / res_time` —— 把一次 LLM 调用变成可日志、可计时的记录。→ 我们的 JSON 日志要含调用次数、latency、tokens。
- `behavior_executor/scripts/sequence_executor`：把 LLM 输出解析成**一行一个 JSON 原子动作**（`{"name": ..., "input": ...}`），name 即原子动作 service 名，逐个执行、失败告警。→ 我们的 ModelPlan 逐 action 下发、policy_id 即执行器服务名、失败即 stop 剩余计划，与此同构。
- 只参考架构；我们不需要 ROS/catkin/BT/SMACH。

## 4. AutoGPT+P（KIT，RSS 2024）— closed-world 对象表示与场景

路径：`autogpt-p/`

要点：
- `autogpt_utility/scene_object.py`：`SceneObject(class_name, id)`，对象名=类名+数字 id（`parse_object_name` 逆解析）；`ObjectRelation(relation_name, *objects)` 表示关系。→ 我们对象 id 用稳定字符串（`red_cube_0`），闭世界对象表 + 谓词（on_table/holding/on/hand_empty）表示状态与 goal，思路一致但不用 PDDL。
- 场景相关：`autogpt_p/evaluation/util/simulated_scene.py`、`data/evaluation/scenes/`（faster_scene_creation.py）—— 批量生成场景与 benchmark。→ 我们 40 任务难度规则生成器（Easy/Medium/Hard/Infeasible）将来参考其"规则化生成 + 校验场景合法性"的做法。

## 落地映射（用于 ch3-planning 骨架）

| 参考做法 | 我们的落点 |
| --- | --- |
| pydantic 结构化输出 + JSON Schema | `schema/model_plan.py` |
| 技能枚举 + 条件必填参数 | `capability/registry.py` + capability 校验 |
| 动作级/任务级成功分离 | `state/feedback.py`（per-step）+ `goal/goal_checker.py`（goal ⊆ S_T） |
| LLM 调用 success/info/耗时 | `logger/episode_logger.py` + planner 调用层（阶段 2 接 VLM） |
| 逐原子动作执行、失败停 | `compiler/executable_plan.py`（policy 映射），真机在 AD-Flow Adapter |
| checker 开关消融 | `protocols/frozen_design.py`（B1/B2a/B2b 开关）+ 实验脚本（阶段 3） |
| 闭世界对象/谓词 | validator object/state 层 + 任务场景 JSONL（阶段 3） |
