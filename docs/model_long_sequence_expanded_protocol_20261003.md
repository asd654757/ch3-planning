# 六技能持续场景：修正后端后的自然执行扩量协议

## 主题与冻结条件

直接检验大模型机器人规划及执行反馈剩余任务修复，不将固定控制器改善或额外观察效果单独归因为模型贡献。

- 30个新seed：3至32，每个seed运行MODEL_NO_PLAN_REPAIR与MODEL_DIAGNOSTIC_REPAIR，共60个episode。
- 每个episode只重置一次，连续执行蓝色中间交付、蓝色返回交付、黄色最终交付，共6个技能。
- 两种设置统一pick-contact-offset=0.030 m、相同视觉定位与最多2轮目的区域再观察、相同控制预算和成功检查。
- 同seed共享初始模型候选，交替执行设置顺序；初始规划与失败修复真实调用模型并记录审计。
- execution-recovery开启，但只有允许修复的设置请求执行修复；证据不足安全停止。
- 不注入故障、不运行中调参、不按结果替换seed。保留模型/API失败与执行失败，按全部30个配对seed报告。
- 旧高度配置结果与3-seed标定pilot不混入新批次。

## 报告与边界

报告全部任务成功率、配对胜负、实际初始/计划拒绝/执行修复调用次数、修复接受次数、发生修复的任务最终成功率、失败阶段、调用和token成本。若有差异，后续需配对检验；30个seed不是60个独立任务。

若没有触发执行修复，即使成功率高也只能说明可执行性，不能证明修复有效。若恢复提高成功率，目前只能归因于状态证据检查、额外恢复动作与模型后缀生成的组合；缺少同证据同预算固定恢复对照，不能证明模型优于固定重试。

本批为受限已知颜色/支撑平面的自然执行实验，不是开放世界状态估计，也不是完整ROUTED性能基准。保存全部数据，不自动接更大批次。

## 运行入口

使用scripts/model_sequence_pilot.py，参数：--seeds 30 --seed-start 3 --long-task --execution-recovery --pick-contact-offset .03。
服务器路径指针：/tmp/model_long_calibrated_formal_pid、/tmp/model_long_calibrated_formal_log、/tmp/model_long_calibrated_formal_output。
