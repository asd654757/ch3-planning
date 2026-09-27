# P2 反事实：强制释放前置条件（零 token）

规则：观测态说某手臂持着 X，而计划在该手臂上的下一个动作不是 `place(X, ...)`，就在它前面插入 `place(table 方向) 释放 X`。评分器是冻结的四层 Validator ＋ Goal Checker（与 428 头条同口径）。

重打分器与冻结 `recovery_success` 的一致性：1284/1284 = 1.0000（不一致清单在 summary.json 里，必须先看这一行再看下表）

| 臂 | 点数 | before | after | 插入释放的点 | 修好 | 改坏 | 精确 McNemar p | 均值动作 before→after | 冻结形态可执行 before→after | 修好但形态被拒 |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|---:|
| ROUTED | 428 | 308 (0.7196) | 347 (0.8107) | 81 | 39 | 0 | 3.638e-12 | 0.916→1.105 | 231→192 | 39 |
| R2_STATE | 428 | 299 (0.6986) | 335 (0.7827) | 77 | 36 | 0 | 2.91e-11 | 0.951→1.131 | 225→189 | 36 |
| R1_FROM_STATE | 428 | 293 (0.6846) | 332 (0.7757) | 80 | 39 | 0 | 3.638e-12 | 0.893→1.079 | 219→180 | 39 |

## 残余失败

- `ROUTED`：残余 81 点，按扰动 `{"wrong_held_object": 59, "grasp_failure": 22}`，首错码 `{"OBJECT_NOT_HELD": 56, "SCHEMA_ERROR": 19, "ARM_NOT_EMPTY": 5, "NONE": 1}`
- `R2_STATE`：残余 93 点，按扰动 `{"wrong_held_object": 68, "grasp_failure": 25}`，首错码 `{"SCHEMA_ERROR": 70, "OBJECT_NOT_HELD": 22, "NONE": 1}`
- `R1_FROM_STATE`：残余 96 点，按扰动 `{"wrong_held_object": 62, "grasp_failure": 33, "object_displacement": 1}`，首错码 `{"OBJECT_NOT_HELD": 69, "SCHEMA_ERROR": 19, "ARM_NOT_EMPTY": 8}`
