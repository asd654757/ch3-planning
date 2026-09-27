# P1 符号规划器基线结果（2026-09-26）

对应审稿条目 C5。脚本 `scripts/symbolic_planner_baseline.py`，零 VLM 调用、零 token，
未改动 `data/collections/` 任何冻结数据。**本文引用的产物全部是修复平手规则（1.5 节）之后的确定性重跑**：

- `data/reports/symbolic_planner_formal428_20260926_det163525/`（428 点 × 3 模式，符号层，0.57 s）
- `data/reports/symbolic_planner_fair100_20260926_det1640/`（100 点 × 3 模式，端到端，380.4 s，含 300 次仿真回合）
- `data/reports/reseed_stability_20260926_det/`（场景重盐稳定性研究，见 3.2 节）
- `data/reports/plan_identity_fair100_det1640.json`（符号计划 vs ROUTED 计划的逐字段对照，见第三节；
  同一脚本跑修复前产物得到 `plan_identity_fair100_prefix_045545.json`，两份对照见 1.5 节）
- `data/reports/p1_determinism_evidence_20260926.json`（平手规则修复前后对照 ＋ 手持守卫消融，见 1.5 节与第一节末）

修复前的中间产物 `symbolic_planner_formal428_20260926_045106/` 与
`symbolic_planner_fair100_20260926_045545/` 只作过程记录：428 侧与确定性重跑**逐字段全同**
（1284/1284），Fair-100 侧 300/300 条只有臂名不同、成功率与各层判定不变，因此本文不引用其数字。
`data/reports/symbolic_planner_fair100_20260926_044237/` 是改用共享场景盐之前的中间产物
（三种模式各自当盐，得到 98/99/98），同样保留但不引用。

## 一、设定

同一注册动作集合、同一冻结评测器（Validator 四层 + Goal Checker + MetaWorld 执行），
把"确定性符号搜索直接生成剩余计划"作为第 5 个基线。三种搜索模式并列报告，避免稻草人：

| 模式 | 接受判据 | 用途 |
|---|---|---|
| `BFS_SHORT` | 只看 goal（与已有 P0 可行性证书同口径） | 与 428 证书表对齐 |
| `BFS_VALID` | goal + 不得以持物结尾（即本文验证器的完整性规则） | 428 符号层头条基线 |
| `BFS_EXEC` | `BFS_VALID` 再限制在冻结执行协议能单回合跑的形态（pick/place 成对、单个 push、单个 press，且非空） | 端到端基线 |

深度上限 8 步，与 P0 证书一致。搜索耗时（确定性重跑产物）：428 1284 次搜索均值 0.149 ms、最大 1.361 ms，
全流程合计 0.57 s；Fair-100 300 次搜索均值 0.293 ms、最大 1.220 ms。
另外加了一条比评测器更严的守卫：手持物体不可再被另一条手臂 pick/push/press
（冻结模拟器在被持有物体的 `at` 字段上是陈旧的）。这条守卫在这两个协议上是惰性的：
把 `single_step_expansions` / `pair_expansions` 里的 `held` 置空后原地重跑
（`data/reports/ablation/planner_guard_off.py`，仅改这一处，其余代码与冻结规划器逐行相同），
428 的 1284 次搜索与 Fair-100 的 300 次搜索给出的后缀**逐字段全同**（1284/1284、300/300，
`search_reason`/`symbolic_valid`/`goal_satisfied`/`final_success` 也全部相同），
三个模式的每一列统计也逐位相同——即符号搜索没有靠这个漏洞得分，也没有被它削弱。
逐字段对照见 `data/reports/p1_determinism_evidence_20260926.json` 的
`guard_on_vs_off_formal428` / `guard_on_vs_off_fair100`。

### 1.5 一个真实的可复现性缺陷：平手由哈希决定（本轮已修）

这不是推测出来的洁癖，而是本轮改动过产物数字的唯一一处。原来的 `search_suffix` 把
`arm_set = set(arms)` 传给扩展函数，而等代价计划的平局由出队顺序决定，
`set[str]` 的迭代顺序又随 `PYTHONHASHSEED` 变——于是**左右臂可互换的点，同一份输入在不同进程里
会给出不同臂的计划**，即基线自身不可复现。修复是把臂顺序固定为 `tuple(sorted(arms))`
（`scripts/symbolic_planner_baseline.py` 的 `search_suffix` docstring 记录了原因），
并在 `PYTHONHASHSEED=1/7` 下各跑一遍 428：1284 条记录逐字段全同
（同一 JSON 的 `hash_seed_1_vs_7_formal428`）。

影响范围（`data/reports/p1_determinism_evidence_20260926.json` 的
`pre_fix_vs_deterministic_*`）：

| 对照 | 条数 | 后缀有差异 | 差异内容 | 任何一层判定/成功率变化 |
|---|---:|---:|---|---|
| State Recovery-428（3 模式 × 428） | 1284 | 0 | — | 无（428/428、321/428 等全部不变） |
| Fair-100（3 模式 × 100） | 300 | 300 | **只有 `arm` 字段**（左↔右），技能/物体/目标/步数全同 | 无（三模式均 100/100，`symbolic_valid`/`goal_satisfied`/`sim_success` 逐条不变） |

结论必须写实：本文第二节、第三节的所有成功率与 McNemar 数字在修复前后**完全相同**，
所以本文的论证不依赖这次修复；被修复推翻的是"两侧计划是否逐字段相同"这句话的口径。
同一份对照脚本（`scripts/plan_identity_check.py`）跑在两个产物上：

| Fair-100 的 `BFS_EXEC` vs ROUTED 四元组对照 | 修复前产物（`..._045545`） | 确定性产物（`..._det1640`） |
|---|---|---|
| 三元组 `(skill, object, target)` 逐点相同 | 100/100 | 100/100 |
| 四元组（再加 `arm`）逐点相同 | 67/100 | **33/100** |
| 只差臂的点数与构成 | 33 点，全是 press | 67 点，34 pick_place ＋ 33 push |

两个产物的三元组都是 100/100、`other_differences` 都是空，也就是说"计划相同"这件事本身是稳的；
**变的只有平手方向**，所以旧版那句"四元组逐点完全相同 67/100"是哈希运气给的（第三节已按确定性产物改写）。
平手规则本身是人为约定（选字典序较小的臂），不改变任何可行性或成功率主张。

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
它按语义直觉得出单个动作，被执行状态层挡下。这与 P2（硬释放前置条件）的动机直接对应，
P2 已量化：见 `data/reports/release_precondition_p2_findings_20260926.md`
（一句规则白拿 ROUTED ＋39／0 回退，p=3.6e-12，只收掉这 120 点差距的 1/3）。
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
这张表由 `scripts/plan_identity_check.py` 在确定性产物上算出
（`data/reports/plan_identity_fair100_det1640.json`），不是人工比对：

| 对照项（`BFS_EXEC` vs ROUTED，同一 100 点） | 数值 |
|---|---|
| 动作数逐点相等 | 100/100（两侧均值都 1.340；66 点 1 动作、34 点 2 动作，动作数直方图逐项相同） |
| `(skill, object_id, target_id)` 三元组逐点相同 | **100/100**（`other_differences` 为空） |
| 再加 `arm` 的四元组逐点相同 | 33/100（全部是 press 族） |
| 其余 67 点的差异 | **只差 `arm` 一个字段**，方向单调：符号侧左臂 67/67、ROUTED 右臂 67/67；技能、物体、目标、步数全同。子集构成 34 pick_place ＋ 33 push |
| 这 67 点上两个臂各自过冻结评测器 | **67/67 两个计划都 valid 且达成目标**（`arm_admissibility.counts.both_ok=67`，`rejected` 空列表） |

即在统一条件的 Fair-100 上，大模型给出的修复计划在功能上与 8 步 BFS 输出同一个计划：
"做什么动作、对哪个物体、放到哪里、几步"逐点一致，唯一分歧是可互换的执行手臂。
臂之所以是平手而不是偏好：注册表里 `pick`/`push`/`press` 的前置只有 `hand_empty(arm)`
（`config/capability_registry.yaml`，`place` 则跟着同一条臂的 `holding(arm, object)`），
而这些点上两条手臂都是空的，所以左右都合法（最后一行就是这件事的直接验证，
用冻结的四层 Validator ＋ Goal Checker 打分，不是我的自定义判据）。
修复后符号侧按 1.5 节的字典序一律取左臂，ROUTED 的冻结计划在 pick/push 上一律取右臂、
在 press 上一律取左臂，因此四元组重合恰好落在 press 的 33 点上。
**旧版笔记里"四元组逐点完全相同 67/100、其余 33 点全是 press 只差臂"是把哈希平手当成了方法属性，方向正好反了，已按确定性口径替换。**
任何"LLM 计划更简洁/更贴合任务"的写法在这份数据上都拿不到支持。

配对（单抽签口径，`BFS_EXEC` 100 vs 各冻结臂；**这张表受 3.1 的混杂污染，只能当描述统计**）：

| 参照臂 | 参照成功 | only SYMBOLIC | only REF | 精确 McNemar p |
|---|---:|---:|---:|---:|
| ROUTED | 98 | 2 | 0 | 0.50 |
| R1_FROM_STATE | 98 | 2 | 0 | 0.50 |
| SELF_REFINE_STATE_V2 | 95 | 5 | 0 | 0.0625 |
| CHECKER_LOOP_STATE_V2 | 71 | 29 | 0 | 3.73e-09 |

ROUTED 这一轮实际花费 104,131 token、100 次模型调用、累计 122.6 s 模型延迟
（1.23 s/点，冻结产物 `cost` 字段）；`BFS_EXEC` 是 0 次调用、0 token、搜索 0.293 ms/点。
2/0 的不一致在 100 点、单抽签口径下仍不显著（p=0.5）。
428 侧同框：ROUTED 480,233 token / 428 点。

### 3.1 必须先说清的协议混杂：场景是按臂重采样过的

冻结执行器把臂名混进 `stable_seed`（`sha256(task_id, seed, arm, pair_index)`），
所以每个臂是在**各自重新随机化的初始物体摆放**里跑一回合。这意味着：

- 上表的 100 vs 98 **不是**同一场景下的对照，只是"各自的运气"；
- 同一条固定计划换个盐重跑，结果会翻转——Fair-100 的所有单抽签端到端数字都带这个噪声
  （428 只在符号层计分，不含仿真执行，不受此项影响）。

第二条不是推测，而是这 2 个不一致点的直接证据：ROUTED 失在
`pick_place_008/seed8` 与 `pick_place_007/seed31`，而符号规划器在这两点给出的计划在
`(skill, object, target)` 上与 ROUTED **逐字段相同**
（`pick(white_cube_16) → place(white_cube_16 → brown_bowl_17)`、
`pick(pink_block_14) → place(pink_block_14 → white_box_15)`），只差臂：符号侧左臂、ROUTED 右臂，
而左臂版本执行成功。臂名进种子 ⇒ 功能上相同的两次计划跑在两个不同的初始摆放里，
一次成一次败 ⇒ 差异来自场景抽签，不来自计划本身（同一批点在 3.2 的共享盐配对下两臂 5/5 同步）。
换句话说，把 2/0 当作"符号搜索端到端优于 ROUTED"的证据是错的。

第一轮实现里三种模式各自用了不同的 `mode` 字符串当盐，得到 98/99/98 且失败点互不重叠，
当时把它读成"接触执行层瓶颈"。改用共享场景盐（`SYMBOLIC_BFS`）重跑后，三个模式都变 100/100，
说明那 2 个失败是场景抽样而不是方法失效。**这一节是本轮实验里最值得写进限制段的东西**：
单抽签的端到端协议在小样本上的区分度低于场景噪声。

### 3.2 场景重盐稳定性研究（去掉这个混杂）

`scripts/reseed_stability_study.py`：对全部 100 个点取符号规划器的 `BFS_EXEC` 计划与
ROUTED 的冻结计划，在同样的 5 个场景盐下各执行一次（两臂同盐 ⇒ 同一初始摆放），
共 1000 次执行、1078.4 s、零 VLM 调用。产物
`data/reports/reseed_stability_20260926_det/`（输入是 1.5 节修复后的确定性符号计划）。
旧产物 `reseed_stability_20260926_050608/` 用修复前的计划，总量数字与本轮相同、
计划同一性拆分的两个子集正好互换，保留作过程记录，不引用其拆分。

配对结果（500 个 cell 对 = 100 点 × 5 盐）：

| 指标 | SYMBOLIC | ROUTED |
|---|---:|---:|
| cell 成功 / 总数 | 488 / 500（0.976） | 488 / 500（0.976） |
| 点级 5/5 全成 | 88 | 88 |
| 点级 4/5（换场景会翻） | 12 | 12 |
| 点级 ≤3/5 | 0 | 0 |

- 不一致 cell：`only_SYMBOLIC` **0**、`only_ROUTED` **0**、两者同成 488、两者同败 12，
  精确 McNemar **p=1.0**。点级多数判据下 100/100 点都是 `both_reliable`，
  `symbolic_only_reliable_points` 与 `routed_only_reliable_points` 都是空列表，
  没有任何一个点其中一臂可靠更好。
- 按"两臂计划是否逐字段相同"拆开看（这一步必须做，否则 p=1 会被高估）：

| 子集 | 点数 | cell 对 | 逐 cell 不一致 | 场景脆弱点 |
|---|---:|---:|---:|---|
| 计划四元组完全相同（press） | 33 | 165 | 0 | 0（165/165 全成） |
| 计划只差 `arm` 字段（34 pick_place ＋ 33 push） | 67 | 335 | **0** | 12（全部 pick_place，两臂在**同一个盐**上一起翻） |

  第一行的 0 不一致是**评测台自身的确定性自检**（同计划同种子必然同结果），不是跨臂证据；
  真正的跨臂证据全部在第二行：67 个点上两臂跑的是完全相同的物体与目标、只有左右臂不同，
  335 个配对 cell 里 `discordant_cells=0`，连那 12 个"换场景会翻"的 pick_place 点也是
  在同一个盐上同时翻（脆弱点清单见 summary 的 `scene_fragile_list`）。
  即注册表意义下两臂可互换这一点，在接触执行层同样成立，且这次覆盖到了会翻的点，
  不再像修复前那轮只压到 165 个必然全成的 press cell 上。

- **单次抽签的分辨率**：每个 cell 的失败率 12/500 = 2.4%，且失败全部集中在 12 个
  "4/5"点上。这就是说 Fair-100 的单抽签口径下，任何一臂的 2 点损失（98/100）
  正好是这个抽样噪声的预期个数（100 × 2.4% = 2.4）。
  进一步坐实：ROUTED 冻结记录里失的那两点（`pick_place_008/seed8`、`pick_place_007/seed31`）
  属于"只差臂"子集，物体/目标/步数与符号计划完全相同，而两臂在本次 5 个共享盐下
  都是 5/5 全成（该点不在 12 个脆弱点里）——
  即那 2 点是同一（功能）计划在未被采样的那个场景里运气不好。
  结论：100 点 × 1 抽签的端到端协议分辨不了这两个方法，"100 vs 98"两边都不能当结论写。
  顺带保留 3.1 的口径：把冻结协议自己的单抽签结果按 5 个盐摊开看是
  SYMBOLIC 500/500、ROUTED 490/500（`single_draw_protocol_success` 字段，只是把冻结的单抽签重复计数，
  不是新测量），与 3.1 的 100 vs 98 一致。

产物：`cells.jsonl`（1000 行 cell 记录，含每 cell 实际执行的动作与臂）、
`reseed_stability_summary.json`（含上面的 plan-identity 拆分与脆弱点清单）、
运行日志 `data/reports/logs/reseed_det.log`。本轮是一次全新执行（日志从 point=1/100 开始，
未用 `--resume`、未用 `--summary-only`），summary 里 `elapsed_s`=1078.4 即执行 wall time。

## 四、能直接写进正文的结论

1. 在给定闭世界符号状态与符号化目标事实的前提下，深度 8 的确定性符号规划器在同一注册动作集合上
   达到 428/428，显著优于 ROUTED 的 308/428（120 个不一致点全部偏向符号搜索，反向 0 个，
   精确 McNemar p=1.5e-36）。
2. 端到端在配对场景控制下**完全分不开**：Fair-100 的 100 点 × 5 盐、1000 次执行里两臂
   500 个 cell 对的不一致数是 0（同成 488、同败 12，精确 McNemar p=1.0），
   点级多数判据 100/100 都是"两臂同样可靠"。符号侧 0 调用 0 token；
   ROUTED 侧 104,131 token、100 次调用、122.6 s 模型延迟。
3. 更强的是等价性而非优越性：Fair-100 上两侧计划在 100/100 点上动作数相同，
   `(skill, object, target)` 三元组 100/100 逐字段相同，67 点只差可互换的臂字段（符号左臂 vs ROUTED 右臂），
   而这 67 点用冻结评测器分别打分是 67/67 两臂都合法且达成目标；在逐盐配对的物理执行下
   这 67 点的 335 个 cell 对**逐 cell 一致**（`discordant_cells=0`），连其中 12 个换场景会翻的
   pick_place 点也是同一个盐上一起翻（3.2）。
   ROUTED 的 2 个失败点上符号规划器给出的是**除臂外相同的计划**并 5/5 执行成功（3.1、3.2）。
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
`(skill, object, target)` 全同，67 点的差异只在可互换的臂上）。因此"LLM 带来更高恢复成功率"这一句在现有两个协议上不能写，
"LLM 计划更贴合任务"也拿不到支持；4.3 的卖点必须改写为"可解释性与接口成本"。

再加一条本轮新增的、必须一起交代的可复现性事实（1.5 节）：修复前基线自身的左右臂由哈希平手决定，
所以任何形如"两侧计划有 N/100 点逐字段相同"的对照在原口径下是**不可复现的**，
不能作为证据写进正文；可写的版本是修复后的确定性口径（三元组 100/100、四元组 33/100、
67 点只差臂且两臂均合法）。这不改变本文任何成功率或 p 值，但改变了"计划同一性"这句话的成立方式。
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

# 符号层，秒级（本文引用 ..._det163525）
$PY scripts/symbolic_planner_baseline.py --protocol formal428 \
    --output-dir data/reports/symbolic_planner_formal428_<TS>

# 端到端，约 6.3 分钟（300 次仿真回合；本文引用 ..._det1640，380.4 s）
$PY scripts/symbolic_planner_baseline.py --protocol fair100 \
    --output-dir data/reports/symbolic_planner_fair100_<TS>

# 平手规则可复现性自检：换哈希种子跑两次 428，逐字段 diff 必须为 0
PYTHONHASHSEED=1 $PY scripts/symbolic_planner_baseline.py --protocol formal428 \
    --output-dir data/reports/ablation/formal428_hseed1
PYTHONHASHSEED=7 $PY scripts/symbolic_planner_baseline.py --protocol formal428 \
    --output-dir data/reports/ablation/formal428_hseed7
$PY data/reports/ablation/diff_cases.py \
    --a data/reports/ablation/formal428_hseed1/symbolic_planner_cases.jsonl \
    --b data/reports/ablation/formal428_hseed7/symbolic_planner_cases.jsonl

# 手持守卫消融（guard-off 是冻结规划器只改 held 一行的副本；产物在 ..._guardoff）
$PY data/reports/ablation/planner_guard_off.py --protocol formal428 --no-sim \
    --output-dir data/reports/ablation/formal428_guardoff
$PY data/reports/ablation/planner_guard_off.py --protocol fair100 --no-sim \
    --output-dir data/reports/ablation/fair100_guardoff
$PY scripts/symbolic_planner_baseline.py --protocol formal428 \
    --output-dir data/reports/ablation/formal428_guardon
$PY scripts/symbolic_planner_baseline.py --protocol fair100 --no-sim \
    --output-dir data/reports/ablation/fair100_guardon

# 计划同一性对照（第三节表；--check-arm-admissible 用冻结评测器给两个臂各自打分）
$PY scripts/plan_identity_check.py \
    --symbolic data/reports/symbolic_planner_fair100_20260926_det1640/symbolic_planner_cases.jsonl \
    --check-arm-admissible > data/reports/plan_identity_fair100_det1640.json

# 上面几步的逐字段汇总，一次写出 p1_determinism_evidence_20260926.json
$PY data/reports/ablation/collect_evidence.py \
    --output data/reports/p1_determinism_evidence_20260926.json

# 场景重盐稳定性研究，约 18 分钟（1000 次仿真回合，1078.4 s），支持 --resume
# 事后换统计口径用 --summary-only（不重跑仿真，只重写 reseed_stability_summary.json）
$PY scripts/reseed_stability_study.py \
    --symbolic-cases data/reports/symbolic_planner_fair100_20260926_det1640/symbolic_planner_cases.jsonl \
    --salts 5 --output data/reports/reseed_stability_20260926_det/cells.jsonl
```
