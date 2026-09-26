# 状态腐蚀扫描（审稿条目 C2 / P3）

- 点数 20，规划器 `BFS_VALID`，动作上限 8
- belief：验证器与目标都按**被相信的状态**判（系统自报口径）
- truth：验证器与目标都按**真实冻结状态**判（真实执行后果）
- confidently wrong：belief 判成功而 truth 目标未达成
- `none` 是控制条件，用来自检本脚本与 P1 的 `BFS_VALID` 是否一致

| Corruption | Applied | Search ok | Mean actions | Empty plan | belief success | truth valid | truth success | Conf. wrong | p vs none |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| none | 0/20 | 1.0000 | 1.7500 | 0.2500 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | - |
| holding_hidden | 5/20 | 1.0000 | 2.0000 | 0.0000 | 1.0000 | 0.0000 | 0.0000 | 1.0000 | 0.0625 |
| object_loc_wrong | 20/20 | 0.6000 | 1.1000 | 0.5000 | 0.6000 | 0.8500 | 0.4500 | 0.1500 | 0.0010 |
| location_swap | 0/20 | 1.0000 | 1.7500 | 0.2500 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 1.0000 |
| goal_fact_added | 15/20 | 1.0000 | 2.3333 | 0.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 1.0000 |
| phantom_object | 20/20 | 1.0000 | 1.7500 | 0.2500 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 1.0000 |

## holding_hidden：按扰动类型分解

| Perturbation | Points | truth success | Conf. wrong |
|---|---:|---:|---:|
| wrong_held_object | 5 | 0.0000 | 1.0000 |

truth 侧首错码：`{"ARM_NOT_EMPTY": 5}`
（b=5, c=0）

## object_loc_wrong：按扰动类型分解

| Perturbation | Points | truth success | Conf. wrong |
|---|---:|---:|---:|
| grasp_failure | 5 | 0.6000 | 0.0000 |
| nominal_state | 5 | 0.4000 | 0.6000 |
| object_displacement | 5 | 0.4000 | 0.0000 |
| wrong_held_object | 5 | 0.4000 | 0.0000 |

truth 侧首错码：`{"STATE_TRANSITION_ERROR": 3}`
（b=11, c=0）

## location_swap：按扰动类型分解

| Perturbation | Points | truth success | Conf. wrong |
|---|---:|---:|---:|
| grasp_failure | 5 | 1.0000 | 0.0000 |
| nominal_state | 5 | 1.0000 | 0.0000 |
| object_displacement | 5 | 1.0000 | 0.0000 |
| wrong_held_object | 5 | 1.0000 | 0.0000 |

## goal_fact_added：按扰动类型分解

| Perturbation | Points | truth success | Conf. wrong |
|---|---:|---:|---:|
| grasp_failure | 5 | 1.0000 | 0.0000 |
| object_displacement | 5 | 1.0000 | 0.0000 |
| wrong_held_object | 5 | 1.0000 | 0.0000 |

## phantom_object：按扰动类型分解

| Perturbation | Points | truth success | Conf. wrong |
|---|---:|---:|---:|
| grasp_failure | 5 | 1.0000 | 0.0000 |
| nominal_state | 5 | 1.0000 | 0.0000 |
| object_displacement | 5 | 1.0000 | 0.0000 |
| wrong_held_object | 5 | 1.0000 | 0.0000 |
