# ROUTED 第三章任务监督与恢复

本仓库实现基于执行证据和任务契约的机器人任务监督，包含历史补证、单步派发、有界恢复与经核验的失败知识复用。当前验证覆盖有限符号回放与使用 RGB 和本体观测的固定目标 MuJoCo 仿真。实验、论文主张和适用范围见[第三章证据核验](docs/ch3_innovation_assessment_20261119.md)。

第一阶段基础设施仍包括以下模块：
- `config/capability_registry.yaml`：能力注册表（pick/place → AD-Flow grasp/place policy 映射）
- `ch3/schema`：ModelPlan / Goal / JSON Schema（pydantic）
- `ch3/validator`：四层确定性校验（syntax/object/capability/state）+ 首错定位
- `ch3/state`：离散世界状态 + `step(state, action)` 模拟器 + Execution Feedback 接口
- `ch3/goal`：goal ⊆ S_T 判定（Repair Success / pass-but-wrong 的基础）
- `ch3/compiler`：ModelPlan → ExecutablePlan（补 policy_id）
- `ch3/logger`：episode JSON 日志模板
- `ch3/protocols`：冻结协议（B0/B1/B2a/B2b、R0/R1/R2、错误码、Repair Success）

环境（数据盘内）：
```bash
cd /root/autodl-tmp/ch3-planning
./.venv/bin/python -m pytest tests -q        # 跑测试
PYTHONPATH=. ./.venv/bin/python scripts/demo_offline_validation.py   # 离线演示
```

设计协议见 `docs/frozen-design-2026-09-08.md`；参考代码笔记见 `docs/reference-notes.md`。
