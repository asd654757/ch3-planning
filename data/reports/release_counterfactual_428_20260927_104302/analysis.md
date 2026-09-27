# P2 反事实：强制释放前置条件（零 token）

规则：观测态说某手臂持着 X，而计划在该手臂上的下一个动作不是 `place(X, ...)`，就在它前面插入 `place(table 方向) 释放 X`。评分器是冻结的四层 Validator ＋ Goal Checker（与 428 头条同口径）。

重打分器与冻结 `recovery_success` 的一致性：640/1284 = 0.4984（不一致清单在 summary.json 里，必须先看这一行再看下表）

| 臂 | 点数 | before | after | 插入释放的点 | 修好 | 改坏 | 精确 McNemar p | 均值动作 before→after | 冻结形态可执行 before→after | 修好但形态被拒 |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|---:|
| ROUTED | 428 | 136 (0.3178) | 207 (0.4836) | 81 | 71 | 0 | 8.47e-22 | 1.918→2.107 | 236→165 | 71 |
| R2_STATE | 428 | 130 (0.3037) | 130 (0.3037) | 77 | 0 | 0 | 1 | 1.953→2.133 | 165→165 | 0 |
| R1_FROM_STATE | 428 | 144 (0.3364) | 217 (0.5070) | 80 | 73 | 0 | 2.118e-22 | 1.895→2.082 | 248→175 | 73 |

## 残余失败

- `ROUTED`：残余 221 点，按扰动 `{"nominal_state": 72, "object_displacement": 72, "grasp_failure": 50, "wrong_held_object": 27}`，首错码 `{"NONE": 221}`
- `R2_STATE`：残余 298 点，按扰动 `{"wrong_held_object": 104, "nominal_state": 72, "object_displacement": 72, "grasp_failure": 50}`，首错码 `{"NONE": 298}`
- `R1_FROM_STATE`：残余 211 点，按扰动 `{"nominal_state": 72, "object_displacement": 72, "grasp_failure": 39, "wrong_held_object": 28}`，首错码 `{"NONE": 211}`
