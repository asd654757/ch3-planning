# P1 符号规划器基线结果（2026-09-26）

对应审稿条目 C5。脚本 `scripts/symbolic_planner_baseline.py`，零 VLM 调用、零 token，
未改动 `data/collections/` 任何冻结数据。原始产物（本轮最终版）：

- `data/reports/symbolic_planner_formal428_20260926_045106/`（428 点 × 3 模式，符号层，0.53 s）
- `data/reports/symbolic_planner_fair100_20260926_045545/`（100 点 × 3 模式，端到端，346.75 s，含 300 次仿真回合）
- `data/reports/reseed_stability_20260926_050608/`（场景重盐稳定性研究，见第三节 3.2）

`data/reports/symbolic_planner_fair100_20260926_044237/` 是改用共享场景盐之前的中间产物
（三种模式各自当盐，得到 98/99/98），保留仅作过程记录，不引用其数字。

## 一、设定

同一注册动作集合、同一冻结评测器（Validator 四层 + Goal Checker + MetaWorld 执行），
把"确定性符号搜索直接生成剩余计划"作为第 5 个基线。三种搜索模式并列报告，避免稻草人：

| 模式 | 接受判据 | 用途 |
|---|---|---|
| `BFS_SHORT` | 只看 goal（与已有 P0 可行性证书同口径） | 与 428 证书表对齐 |
| `BFS_VALID` | goal + 不得以持物结尾（即本文验证器的完整性规则） | 428 符号层头条基线 |
| `BFS_EXEC` | `BFS_VALID` 再限制在冻结执行协议能单回合跑的形态（pick/place 成对、单个 push、单个 press，且非空） | 端到端基线 |

深度上限 8 步，与 P0 证书一致。搜索耗时：428 均值 0.146 ms/点、最大 1.326 ms；
Fair-100 均值 0.225 ms、最大 0.467 ms；428 全流程（1284 次搜索）合计 0.53 s。
另外加了一条比评测器更严的守卫：手持物体不可再被另一条手臂 pick/push/press
（冻结模拟器在被持有物体的 `at` 字段上是陈旧的）。这条守卫在这两个协议上是惰性的：
把 `single_step_expansions` / `pair_expansions` 里的 `held` 置空后原地重跑，
428 的 1284 次搜索与 Fair-100 的 300 次搜索给出的后缀**逐字段全同**（1584/1584），
三个模式的每一列统计也逐位相同——即符号搜索没有靠这个漏洞得分，也没有被它削弱。

## 二、State Recovery-428（符号层）

| Mode | Points | Final success | Rate | Mean suffix actions | 配对 vs ROUTED（only SYMBOLIC / only REF / 精确 McNemar p） |
|---|---:|---:|---:|---:|---|
| `BFS_SHORT` | 428 | 321 | 0.7500 | 1.002 | 22 / 9 / 0.0294 |
| `BFS_VALID` | 428 | **428** | **1.0000** | 1.252 | **120 / 0 / 1.50e-36** |
| `BFS_EXEC` | 428 | 321 | 0.7500 | 1.002 | 22 / 9 / 0.0294 |

`BFS_VALID` 另外两个参照臂：vs R2_STATE(299) 129/0 p=2.94e-39，vs R1_FROM_STATE(293) 135/0 p=4.59e-41。
LLM 三臂同框参照：R2_STATE 299（0.6986）、R1_FROM_STATE 293（0.6846）、ROUTED 308（0.7196），
平均修复动作 0.951 / 0.893 / 0.916（冻结产物口径的均值动作开销 −0.051 / −0.110 / −0.086，
基准是 goal-only 证书的均长 1.002）。

分解（`BFS_VALID` 修好而 ROUTED 修坏的 120 点）：wrong_held_object 98 ＋ grasp_failure 22。
逐点回查冻结 formal 产物里 ROUTED 自己的字段（不是扰动注入时的 `stress_validation`）：

| 扰动 | ROUTED 首错码 / 层 | 发出动作数 | 点数 |
|---|---|---:|---:|
| wrong_held_object | `ARM_NOT_EMPTY` / execution_state | 1 | 57 |
| wrong_held_object | `OBJECT_NOT_HELD` / execution_state | 1 | 34 |
| wrong_held_object | `ARM_NOT_EMPTY` / execution_state | 3 | 5 |
| wrong_held_object | `OBJECT_NOT_HELD` / execution_state | 2 | 2 |
| grasp_failure | `OBJECT_NOT_HELD` / execution_state | 1 | 22 |

120 点全部 `valid=false` 且 `goal_satisfied=false`；`prefix_mutation` 全为 False（没动锁定的前缀）；
`unnecessary_action_count` 全为 0（不是动作冗余）。113/120 只发了 **1 个动作**。
也就是说 ROUTED 的失败形态一致：在需要"先放掉手里那个、再重抓/放置"的点上，
它按语义直觉得出单个动作，被执行状态层挡下。这与 P2（硬释放前置条件）的动机直接对应。
R2_STATE、R1_FROM_STATE 的不一致点（129、135）呈同样形态（错误码集中在
`ARM_NOT_EMPTY`/`OBJECT_NOT_HELD`，R1 另有 1 个 `STATE_TRANSITION_ERROR`；
两者的 `unnecessary_action_count` 与 `prefix_mutation` 在不一致点上同样全为 0/False）。

完整机制（107 个错误持物点）：观测状态里右臂持着错误物体，目标事实只提
`on(red_cube_0, blue_tray_1)`。只按 goal 接受时，搜索给出 `pick → place`（1.336 动作/点，
与 P0 证书给出的 goal-only 最短长度完全一致），但验证器的完整性规则因为"结束时仍有手臂持物"
判整段不可执行（`symbolic_valid` 0/107；`goal_satisfied` 记录为 false 是跳过执行的结果，
不是目标事实本身不成立）。加上"不得以持物结尾"这条接受判据后，搜索自动多生成一步
"把手里那个错误物体放掉"（例：`place(right, blue_block_2 → blue_tray_1)` 再 `pick/place` 目标物体），
107/107 全部转为可执行且最终成功。

这条差异的代价是可以精确量化的：**完整性规则在 107 个点上恰好各多 1 个动作**
（`BFS_VALID` 2.336 vs goal-only 证书 1.336，其余三类扰动 overhead 全为 0），
换来 0/107 → 107/107。反过来看 LLM 三臂的平均后缀动作 0.951 / 0.893 / 0.916，
相对 goal-only 证书 1.002 的均值开销是 −0.051 / −0.110 / −0.086（冻结产物口径，跨全部 428 点）：
三个 LLM 臂的问题同样是"少做一步"，不是"多做无用动作"。

`BFS_EXEC` 在 428 上的 107 个 `no_plan_within_cap` 全部落在 wrong_held_object：
错误持物点的正确修复需要"释放＋重抓＋放置"至少 3 个动作，而冻结的 MetaWorld 执行协议
只接受 pick/place 成对或单个 push/press 的非空形态，形态过滤器把这类计划整类拒掉。
即符号搜索在 428 上的瓶颈不是搜索，而是端到端协议的动作形态约束。
`BFS_EXEC` 的另一个约束代价：`nominal_state`（目标本来就已满足、证书长度 0）上它必须给出非空可执行形态，
于是产出等价于空操作的 pick/place 对（均值 1.336 动作，overhead ＋1 有 71 点、＋2 有 36 点）；
端到端头条只报 `BFS_EXEC` 时，这一类的"无意义动作"要一并交代。

## 三、Fair-100 统一条件协议（端到端，含 MetaWorld 执行）

| Mode | Search ok | Symbolic valid | Goal ok | Executable shape | Sim attempted | Final success |
|---|---:|---:|---:|---:|---:|---:|
| `BFS_SHORT` | 100 | 100 | 100 | 100 | 100 | **100 / 100（1.00）** |
| `BFS_VALID` | 100 | 100 | 100 | 100 | 100 | **100 / 100（1.00）** |
| `BFS_EXEC` | 100 | 100 | 100 | 100 | 100 | **100 / 100（1.00）** |

三个模式在 Fair-100 上无差别（100 点全部是"当前可执行形态内 1–2 步可达"的简单后缀：
均值 1.34 动作、最大 2 动作，`no_plan_within_cap` 与 `empty_suffix_goal_holds` 均为 0），
所以后面只报 `BFS_EXEC`。

先给一个比成功率更硬的对照：**符号规划器与 ROUTED 在 100 个点上生成的计划一一对齐**。

| 对照项（`BFS_EXEC` vs ROUTED，同一 100 点） | 数值 |
|---|---|
| 动作数逐点相等 | 100/100（两侧均值都 1.340；66 点 1 动作、34 点 2 动作） |
| `(skill, object, target, arm)` 四元组逐点完全相同 | 67/100 |
| 其余 33 点的差异 | 全部是 press 族，且**只差 `arm` 字段**（left/right），技能、物体、目标、步数全同 |

即在统一条件的 Fair-100 上，大模型给出的修复计划在功能上与 8 步 BFS 输出同一个计划。
那 33 点的臂选择差异不是设计出来的偏好：搜索侧的臂来自集合迭代顺序（未写平手规则），
ROUTED 侧给了左臂，而注册表里 `press` 的前置条件只有 `hand_empty(arm)`、这两臂当时都为空，
所以它是可互换选项；3.2 在逐盐配对照下确认左右臂物理结果同步（165/165）。
任何"LLM 计划更简洁/更贴合任务"的写法在这份数据上都拿不到支持。

配对（单抽签口径，`BFS_EXEC` 100 vs 各冻结臂；**这张表受 3.1 的混杂污染，只能当描述统计**）：

| 参照臂 | 参照成功 | only SYMBOLIC | only REF | 精确 McNemar p |
|---|---:|---:|---:|---:|
| ROUTED | 98 | 2 | 0 | 0.50 |
| R1_FROM_STATE | 98 | 2 | 0 | 0.50 |
| SELF_REFINE_STATE_V2 | 95 | 5 | 0 | 0.0625 |
| CHECKER_LOOP_STATE_V2 | 71 | 29 | 0 | 3.73e-09 |

ROUTED 这一轮实际花费 104,131 token、100 次模型调用、累计 122.6 s 模型延迟
（1.23 s/点，冻结产物 `cost` 字段）；`BFS_EXEC` 是 0 次调用、0 token、搜索 0.23 ms/点。
2/0 的不一致在 100 点、单抽签口径下仍不显著（p=0.5）。
428 侧同框：ROUTED 480,233 token / 428 点。

### 3.1 必须先说清的协议混杂：场景是按臂重采样过的

冻结执行器把臂名混进 `stable_seed`（`sha256(task_id, seed, arm, pair_index)`），
所以每个臂是在**各自重新随机化的初始物体摆放**里跑一回合。这意味着：

- 上表的 100 vs 98 **不是**同一场景下的对照，只是"各自的运气"；
- 同一条固定计划换个盐重跑，结果会翻转——Fair-100 的所有单抽签端到端数字都带这个噪声
  （428 只在符号层计分，不含仿真执行，不受此项影响）。

第二条不是推测，而是这 2 个不一致点的直接证据：ROUTED 失在
`pick_place_008/seed8` 与 `pick_place_007/seed31`，而符号规划器在这两点给出的计划是
**逐字段相同的** `pick(white_cube_16,right) → place(white_cube_16 → brown_bowl_17,right)` 与
`pick(pink_block_14,right) → place(pink_block_14 → white_box_15,right)`，并且执行成功。
同一条计划，一次成一次败 ⇒ 差异全部来自初始摆放抽签，不来自计划。
换句话说，把 2/0 当作"符号搜索端到端优于 ROUTED"的证据是错的。

第一轮实现里三种模式各自用了不同的 `mode` 字符串当盐，得到 98/99/98 且失败点互不重叠，
当时把它读成"接触执行层瓶颈"。改用共享场景盐（`SYMBOLIC_BFS`）重跑后，三个模式都变 100/100，
说明那 2 个失败是场景抽样而不是方法失效。**这一节是本轮实验里最值得写进限制段的东西**：
单抽签的端到端协议在小样本上的区分度低于场景噪声。

### 3.2 场景重盐稳定性研究（去掉这个混杂）

`scripts/reseed_stability_study.py`：对全部 100 个点取符号规划器的 `BFS_EXEC` 计划与
ROUTED 的冻结计划，在同样的 5 个场景盐下各执行一次（两臂同盐 ⇒ 同一初始摆放），
共 1000 次执行、1120.2 s、零 VLM 调用。产物 `data/reports/reseed_stability_20260926_050608/`。

配对结果（500 个 cell 对 = 100 点 × 5 盐）：

| 指标 | SYMBOLIC | ROUTED |
|---|---:|---:|
| cell 成功 / 总数 | 488 / 500（0.976） | 488 / 500（0.976） |
| 点级 5/5 全成 | 88 | 88 |
| 点级 4/5（换场景会翻） | 12 | 12 |
| 点级 ≤3/5 | 0 | 0 |

- 不一致 cell：`only_SYMBOLIC` **0**、`only_ROUTED` **0**、两者同成 488、两者同败 12，
  精确 McNemar **p=1.0**。点级多数判据下 100/100 点都是 `both_reliable`，
  没有任何一个点其中一臂可靠更好。
- 按"两臂计划是否逐字段相同"拆开看（这一步必须做，否则 p=1 会被高估）：

| 子集 | 点数 | cell 对 | 不一致 cell | 场景脆弱点 |
|---|---:|---:|---:|---:|
| 计划完全相同 | 67 | 335 | 0 | 12（全部 pick_place） |
| 计划只差 press 的 `arm` 字段 | 33 | 165 | 0 | 0（165/165 全成） |

  第一行的 0 不一致是**评测台自身的确定性自检**（同计划同种子必然同结果），不是跨臂证据；
  真正的跨臂证据在第二行：press 换左臂/右臂在完全相同场景下 165/165 与 ROUTED 同步，
  即注册表意义下两臂可互换这一点在物理执行层也成立。

- **单次抽签的分辨率**：每个 cell 的失败率 12/500 = 2.4%，且失败全部集中在 12 个
  "4/5"点上。这就是说 Fair-100 的单抽签口径下，任何一臂的 2 点损失（98/100）
  正好是这个抽样噪声的预期个数（100 × 2.4% = 2.4）。
  进一步坐实：ROUTED 冻结记录里失的那两点（`pick_place_008/seed8`、`pick_place_007/seed31`）
  与符号规划器的计划**逐字段相同**，而该计划在本次 5 个共享盐下两臂都 5/5 全成——
  即那 2 点是同一计划在未被采样的那个场景里运气不好。
  结论：100 点 × 1 抽签的端到端协议分辨不了这两个方法，"100 vs 98"两边都不能当结论写。

产物：`cells.jsonl`（1000 行 cell 记录）、`reseed_stability_summary.json`（含上面的
plan-identity 拆分）、运行日志 `logs/reseed_stability_20260926_050608.log`。
该 summary 在跑完后用 `--summary-only` 重算过一次（加了计划同一性拆分），
所以里面带 `summarize_only: true`，而 `elapsed_s`=1120.2 与 `cells_completed_at_utc`
是从执行那一轮的 sidecar 继承的。

## 四、能直接写进正文的结论

1. 在给定闭世界符号状态与符号化目标事实的前提下，深度 8 的确定性符号规划器在同一注册动作集合上
   达到 428/428，显著优于 ROUTED 的 308/428（120 个不一致点全部偏向符号搜索，反向 0 个，
   精确 McNemar p=1.5e-36）。
2. 端到端在配对场景控制下**完全分不开**：Fair-100 的 100 点 × 5 盐、1000 次执行里两臂
   500 个 cell 对的不一致数是 0（同成 488、同败 12，精确 McNemar p=1.0），
   点级多数判据 100/100 都是"两臂同样可靠"。符号侧 0 调用 0 token；
   ROUTED 侧 104,131 token、100 次调用、122.6 s 模型延迟。
3. 更强的是等价性而非优越性：Fair-100 上两侧计划在 100/100 点上动作数相同，
   67 点四元组逐字段相同，剩下 33 点只差 press 的左右臂——而这 33 点在逐盐配对照下
   165/165 与 ROUTED 同步成功，说明注册表里两臂可互换这一点在物理执行层同样成立。
   ROUTED 的 2 个失败点上符号规划器给出的是**完全相同的计划**并 5/5 执行成功（3.2）。
   即在该协议覆盖的任务族内，大模型没有产生符号搜索给不出的计划。
4. 符号搜索自身暴露的缺口是验证器补上的：只按 goal 接受时，`BFS_SHORT` 在全部 107 个错误持物点上
   产出"以持物结尾"的后缀而被验证器判不可执行（0/107）；加入验证器的完整性规则后
   `BFS_VALID` 在这 107 点上全部通过，代价恰好是每点 +1 个动作。
   即验证层即使对一个完美规划器也是承载性的。
5. 方法学结论（单独写进限制段）：Fair-100 单抽签口径的 per-draw 失败率是 12/500 = 2.4%，
   且失败全部落在 12 个"5 次里成 4 次"的点上——100 点 × 1 抽签既分辨不出 100 vs 98，
   也分辨不出任何 2 点级的跨臂差异。要报告端到端差异，每个点必须配 ≥5 次同场景配对执行。

## 五、对 4.3 卖点的冲击（按待补清单的风险条款，不淡化）

清单里预写的风险成立，而且比预期更强：符号基线在两个协议上都不劣于 ROUTED，而且端到端
在配对场景控制下与 ROUTED **逐 cell 一致**（428 符号层 428 vs 308，p=1.5e-36；
Fair-100 端到端 500 个配对 cell 里不一致 0 个，p=1.0，两侧计划本身在 100/100 点上同长、
在 67 点上逐字段相同）。因此"LLM 带来更高恢复成功率"这一句在现有两个协议上不能写，
"LLM 计划更贴合任务"也拿不到支持；4.3 的卖点必须改写为"可解释性与接口成本"。
可写的正面主张收缩为三条，且都需要显式前提：

- 大模型的作用是**接口**：把自然语言指令与感知输出编译成符号搜索所需的闭世界目标事实与状态。
  428 与 Fair-100 都以人工给定的 `goal.facts` 和结构化状态为输入，这一层被协议本身假设掉了。
- 符号搜索需要**可枚举且完备的动作模型**与可信状态；这两条前提一旦不成立（P3 的腐蚀状态、
  未见物体引用），搜索要么无解要么自信地给出错计划。
- 验证器＋能力注册表是**承载性组件**，不是大模型的附属（第四节第 4 条由基线自身反证）。

据此，4.3 需要把"为什么还需要大模型"的回答从成功率改到"符号层输入从哪来"，
并把 P3（状态腐蚀）提到必要性论证的主位——那是目前唯一能用现有数据把差异量化出来的地方。
另外，`BFS_EXEC` 在 428 的 wrong_held_object 上因动作形态约束整类无解（第二节末），
说明"可枚举且完备"这条前提在接触层形态约束下也会失效，可作为 4.3 的一个具体反例。

## 六、复现命令

```bash
cd /root/autodl-tmp/ch3-planning
export PYTHONPATH=. MUJOCO_GL=egl
PY=/root/autodl-tmp/.venvs/metaworld-lerobot/bin/python

# 符号层，秒级
$PY scripts/symbolic_planner_baseline.py --protocol formal428 \
    --output-dir data/reports/symbolic_planner_formal428_<TS>

# 端到端，约 6 分钟（300 次仿真回合）
$PY scripts/symbolic_planner_baseline.py --protocol fair100 \
    --output-dir data/reports/symbolic_planner_fair100_<TS>

# 场景重盐稳定性研究，19 分钟（1000 次仿真回合，1120.2 s），支持 --resume
# 事后换统计口径用 --summary-only（不重跑仿真，只重写 reseed_stability_summary.json）
$PY scripts/reseed_stability_study.py \
    --symbolic-cases data/reports/symbolic_planner_fair100_<TS>/symbolic_planner_cases.jsonl \
    --salts 5 --output data/reports/reseed_stability_<TS>/cells.jsonl
```
