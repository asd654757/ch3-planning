# 自然状态错误：动作历史预期 vs 实际观测（C2 / P3）

- belief：把**已执行前缀**在冻结模拟器里重放得到的状态当作输入（控制器真实持有的信念）
- truth：冻结记录里的**实际观测状态**（基准自己的判分口径）
- truth goal (phys)：在真实观测状态上推进计划到首个 state 层错误后，目标是否成立
- silent failure：系统自报成功，而真实世界没达成目标
- planner on observed state：同一个规划器改喂实际观测状态（= P1 的 `BFS_VALID` 口径）
- 无任何扰动注入、无模型调用、无 token；两侧状态全部来自冻结数据

| Perturbation | Points | belief==obs | belief success | truth success (frozen) | truth goal (phys) | Silent failure | Rejected in truth | planner on observed | Mean actions |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| grasp_failure | 107 | 0/107 | 1.0000 | 0.0000 | 0.0000 | 1.0000 | 0.3364 | 1.0000 | 0.3364 |
| nominal_state | 107 | 107/107 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 1.0000 | 0.0000 |
| object_displacement | 107 | 0/107 | 1.0000 | 0.0000 | 0.0000 | 1.0000 | 0.0000 | 1.0000 | 0.0000 |
| wrong_held_object | 107 | 0/107 | 1.0000 | 0.0000 | 0.3364 | 0.6636 | 1.0000 | 1.0000 | 1.0000 |
| **ALL** | 428 | - | 1.0000 | 0.2500 | 0.3341 | 0.6659 | - | 1.0000 | 0.3341 |

全量配对（喂观测状态 vs 喂预期状态，冻结口径 success）：only_observed=321, only_expected=0, 精确 McNemar p=4.682e-97

## 不看场景的修复策略上界（信息论证）

- 同一预期状态签名 ⇒ 对任何只吃状态的策略不可区分，策略必须整类共用一个计划
- 每类候选 = 类内各点在其真实状态下的最优计划（观测态搜索 / 预期态搜索 / 整条标称计划重放 / 空操作）的并集；
  逐类取 max 再求和 ⇒ 任何『不回看场景』的修复策略（符号或大模型，含未来更强的）都不超过这个数

- 歧义类 84 个；落在多义类里的点 428/428
- 上界（冻结口径 success）：356/428 = 0.8318
- 上界（真实世界达成目标）：428/428 = 1.0000
- 类大小分布（size→count，前 6）：`{"9": 23, "6": 13, "3": 47, "2": 1}`

最大的几个歧义类（同类里扰动类型不同）：

| Points | Perturbations in class | best success | best physical | best plan |
|---:|---|---:|---:|---|
| 9 | grasp_failure, nominal_state, object_displacement | 9 | 9 | `press|black_button_46|None|left` |
| 9 | grasp_failure, nominal_state, object_displacement | 6 | 9 | `push|black_cube_34|goal_pad|left` |
| 9 | grasp_failure, nominal_state, object_displacement | 6 | 9 | `push|blue_block_25|goal_pad|left` |
| 9 | grasp_failure, nominal_state, object_displacement | 9 | 9 | `press|blue_button_37|None|left` |
| 9 | grasp_failure, nominal_state, object_displacement | 6 | 9 | `push|brown_block_33|goal_pad|left` |
| 9 | grasp_failure, nominal_state, object_displacement | 9 | 9 | `press|brown_button_45|None|left` |
| 9 | grasp_failure, nominal_state, object_displacement | 9 | 9 | `press|cyan_button_42|None|left` |
| 9 | grasp_failure, nominal_state, object_displacement | 6 | 9 | `push|cyan_cube_30|goal_pad|left` |

### 对照：确定性兜底策略『忽略状态、重放整条标称计划』

- 冻结口径 success：249/428 = 0.5818
- 真实世界达成目标：321/428 = 0.7500
- 分类：`{"grasp_failure": {"points": 107, "frozen": 107, "physical": 107}, "nominal_state": {"points": 107, "frozen": 35, "physical": 107}, "object_displacement": {"points": 107, "frozen": 107, "physical": 107}, "wrong_held_object": {"points": 107, "frozen": 0, "physical": 0}}`

## 各扰动的真实侧首错码

- `grasp_failure`：冻结口径 `{"OBJECT_NOT_HELD": 36}`；真实 state 层 `{"OBJECT_NOT_HELD": 36}`
- `nominal_state`：冻结口径 `{}`；真实 state 层 `{}`
- `object_displacement`：冻结口径 `{}`；真实 state 层 `{}`
- `wrong_held_object`：冻结口径 `{"OBJECT_NOT_HELD": 36, "SCHEMA_ERROR": 36, "ARM_NOT_EMPTY": 35}`；真实 state 层 `{"OBJECT_NOT_HELD": 36, "ARM_NOT_EMPTY": 35}`
