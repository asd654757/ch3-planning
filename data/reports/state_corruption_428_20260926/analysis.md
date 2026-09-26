# 状态腐蚀扫描（审稿条目 C2 / P3）

- 点数 428，规划器 `BFS_VALID`，动作上限 8
- belief：验证器与目标都按**被相信的状态**判（系统自报口径）
- truth success (frozen)：冻结口径，真实状态上 valid ∧ goal
- truth goal (phys)：真实状态上用状态模拟器推进到首个 state 层错误后，目标事实是否成立
  （冻结口径的 valid 还含『不得以持物结尾』的完整性规则，它会把『世界其实达成了目标』
  与『世界不同意』混在一起，所以两列并报）
- Conf. wrong：belief 判成功而真实世界目标未达成（最危险的一格）
- False alarm：belief 判失败而真实世界已达标（过度保守，白修一次）
- `none` 是控制条件，用来自检本脚本与 P1 的 `BFS_VALID` 是否一致

## 腐蚀算子

| Corruption | 语义 |
|---|---|
| none | control: believed == true (must reproduce P1 ``BFS_VALID``) |
| holding_hidden | the held object is reported as resting on the table, arm empty |
| object_loc_wrong | one object's surface is reported as another surface |
| identity_swap | two objects' reported surfaces are exchanged |
| goal_fact_added | a missing goal fact is reported as already true (illusion of completion) |
| phantom_object | an undetected extra object is added to the believed scene |

| Corruption | Applied | Search ok | Mean actions | Empty plan | belief success | truth success (frozen) | truth goal (phys) | Conf. wrong | False alarm | p vs none |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| none | 0/428 | 1.0000 | 1.2523 | 0.2500 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | - |
| holding_hidden | 107/428 | 1.0000 | 1.3364 | 0.0000 | 1.0000 | 0.0000 | 0.6729 | 0.3271 | 0.0000 | 0.0000 |
| object_loc_wrong | 279/428 | 0.7706 | 1.1111 | 0.3907 | 0.7706 | 0.5090 | 0.6093 | 0.1613 | 0.0000 | 0.0000 |
| identity_swap | 0/428 | 1.0000 | 1.2523 | 0.2500 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 1.0000 |
| goal_fact_added | 321/428 | 1.0000 | 0.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| phantom_object | 428/428 | 1.0000 | 1.2523 | 0.2500 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 1.0000 |

## holding_hidden：按扰动类型分解

| Perturbation | Points | truth success (frozen) | truth goal (phys) | Conf. wrong | False alarm |
|---|---:|---:|---:|---:|---:|
| wrong_held_object | 107 | 0.0000 | 0.6729 | 0.3271 | 0.0000 |

冻结口径首错码：`{"SCHEMA_ERROR": 72, "ARM_NOT_EMPTY": 35}`
真实 state 层首错码：`{"ARM_NOT_EMPTY": 35}`
配对（冻结口径 success）：b=107, c=0

## object_loc_wrong：按扰动类型分解

| Perturbation | Points | truth success (frozen) | truth goal (phys) | Conf. wrong | False alarm |
|---|---:|---:|---:|---:|---:|
| grasp_failure | 72 | 0.5000 | 0.5000 | 0.2778 | 0.0000 |
| nominal_state | 28 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| object_displacement | 72 | 0.4861 | 0.4861 | 0.2222 | 0.0000 |
| wrong_held_object | 107 | 0.6636 | 0.6636 | 0.0841 | 0.0000 |

冻结口径首错码：`{"STATE_TRANSITION_ERROR": 28}`
真实 state 层首错码：`{"STATE_TRANSITION_ERROR": 28}`
配对（冻结口径 success）：b=137, c=0

## identity_swap：按扰动类型分解

| Perturbation | Points | truth success (frozen) | truth goal (phys) | Conf. wrong | False alarm |
|---|---:|---:|---:|---:|---:|
| grasp_failure | 107 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| nominal_state | 107 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| object_displacement | 107 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| wrong_held_object | 107 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |

配对（冻结口径 success）：b=0, c=0

## goal_fact_added：按扰动类型分解

| Perturbation | Points | truth success (frozen) | truth goal (phys) | Conf. wrong | False alarm |
|---|---:|---:|---:|---:|---:|
| grasp_failure | 107 | 0.0000 | 0.0000 | 1.0000 | 0.0000 |
| object_displacement | 107 | 0.0000 | 0.0000 | 1.0000 | 0.0000 |
| wrong_held_object | 107 | 0.0000 | 0.0000 | 1.0000 | 0.0000 |

配对（冻结口径 success）：b=321, c=0

## phantom_object：按扰动类型分解

| Perturbation | Points | truth success (frozen) | truth goal (phys) | Conf. wrong | False alarm |
|---|---:|---:|---:|---:|---:|
| grasp_failure | 107 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| nominal_state | 107 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| object_displacement | 107 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| wrong_held_object | 107 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |

配对（冻结口径 success）：b=0, c=0
