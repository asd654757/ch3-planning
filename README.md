# ch3-planning（第三章 阶段 0/1 工程骨架）

冻结版方案（2026-09-08）的代码落地。仅含**不依赖 VLM/真机**的部分：
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
