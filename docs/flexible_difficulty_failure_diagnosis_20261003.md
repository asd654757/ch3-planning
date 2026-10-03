# 中等/较难反馈恢复：轨迹与公开源码核查

## 数据与结论

正式目录 `data/collections/flexible_difficulty_formal_20261003_095945`，每难度30个配对，不重跑、不筛掉失败。

- medium：直接29/30，反馈29/30，同一seed200未知目标安全停止。
- hard：直接28/30，反馈18/30；直接独有成功10、反馈独有成功0，精确McNemar p=0.001953125。
- hard首次恢复顺序：直接蓝→黄29例成功28例，黄→蓝1例成功0例；反馈蓝→黄19例成功18例，黄→蓝11例成功0例。
- 两方法合计12例首次黄→蓝均失败；47例蓝→黄成功46例。这是轨迹关联，未通过独立顺序干预证明因果。

## 已确认的实现缺口

1. `evaluate_flexible`只检查对象最终位置、手空、禁动物体及每候选最多四次搬运；没有区域占用/释放前置约束。硬任务更新后蓝色仍占return_region，却允许先把黄色放入同一区域。`seed202`的RGB与当前符号事实均可见蓝色占位；此判断不需要仿真真值。
2. `diagnostic_feedback`把真实执行偏差 `OBSERVED_GOAL_NOT_SATISFIED` 归为普通plan_contract_violation，并标记candidate_executed=False。真实执行动作和未执行的拒绝候选应分别建模。当前标记语义混淆；不能断言其单独导致顺序偏好。
3. 真实偏差的完整视觉证据只在本地audit，模型主要收到detached_off_goal与observed_support；缺少失败动作/目标、对象间占用依赖与变化摘要，第二次失败也得到几乎相同诊断。
4. 模型与校验器的“最多四次搬运”是单个候选上限，执行器是整episode最多四次；未传入已尝试次数与剩余次数。seed202/211/212/217在两次原尝试后黄色再次放偏，再接受两次搬运后缀，超过剩余预算；蓝色完成后黄色剩余合法计划仍无法执行。
5. 失败阶段记录有边界问题：总预算在下一轮循环入口耗尽时failure_stage仍是上一次place，不能作为新一次place失败解释。

## 公开项目核查（2026-10-03）

- PragmaBot `pragmabot/src/pragmabot/vlm_task_planner.py`：短期历史包含已执行动作及反馈；强调空间关系与不可执行动作，不立即重复失败动作，选择下一步。
- PragmaBot `vlm_success_detector.py`：前后图像比较，动作成功与任务完成分开。其较宽松成功提示不适用于本项目，不照搬。
- PragmaBot `nodes/pragmabot_node.py`：实际执行部分有实现占位提示，不能当作可直接移植的完整执行器。
- Astra公开 `hybrid_rollout/robolab/robolab_server/gate_assessment.py`：区分上一执行结果与下一动作意图；前置失败不能推进依赖子目标；完成状态基于视觉，后来失效应撤销；不回放、不使用对象真值/奖励/未来状态。这段可核查，不据此推断其全部性能机制。
- llm-as-policies `site/downloads/STUDY_NOTES.md` 与 `PROTOCOL.md`：决策边界反馈、保留原始失败、动作结果与任务成功区别；仓库主要是静态公开材料，不是可直接使用的完整运行控制系统。

源码链接：
- https://github.com/leggedrobotics/pragmabot/blob/main/pragmabot/src/pragmabot/vlm_task_planner.py
- https://github.com/leggedrobotics/pragmabot/blob/main/pragmabot/src/pragmabot/vlm_success_detector.py
- https://github.com/anonymous-report-421/eval-of-gpt-6-astra-as-policy/blob/main/hybrid_rollout/robolab/robolab_server/gate_assessment.py
- https://github.com/cookiegg/llm-as-policies/blob/main/site/downloads/STUDY_NOTES.md

## 下一版本建议（尚未实现/运行）

保持LLM产生剩余计划，不引入固定正确序列或额外控制训练。

1. 类型化反馈：execution_event / candidate_rejection / task_update，携带已执行动作、视觉支持后置条件、证据来源、未知字段；不把符号预测当观测。
2. 从RGB支持的区域占用或有时间标记的最近观测生成release prerequisite；模型生成合法清空占位顺序，Validator只拒绝冲突，不替模型写正确序列。陈旧状态须重观测，不宣称持续真实证书。
3. prompt与candidate gate共享实际剩余搬运预算与调用预算；拒绝超过预算的候选，不给某方法独占更多预算。
4. 保留完整计划作为任务展望，只执行下一合法搬运，再据观测校验后续；不得混淆模型未执行计划与已执行历史。
5. 先单元测试占位交换与预算，再旧失败seed作为开发复现（不算正式验证）；用预冻结独立seed做新配对小试。只有证据门、预算门与真实恢复路径正确才扩量。

不得把本轮重新分类成成功；不得放宽释放/任务评分，不把评分真值注入规划，不固定蓝色优先来制造提升。新前置门及状态事实须在可比方法中共享；若主方法额外引入门，须分别报告机制组合收益与同信息反馈收益。
