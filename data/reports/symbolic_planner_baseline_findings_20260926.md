# P1 符号规划器基线结果（2026-09-26）

对应审稿条目 C5。脚本 `scripts/symbolic_planner_baseline.py`，零 VLM 调用、零 token，
未改动 `data/collections/` 任何冻结数据。原始产物（本轮最终版）：

- `data/reports/symbolic_planner_formal428_20260926_045106/`（428 点 × 3 模式，符号层，0.53 s）
- `data/reports/symbolic_planner_fair100_20260926_045545/`（100 点 × 3 模式，端到端，346.75 s，含 300 次仿真回合）
- `data/reports/reseed_stability_<TS>/`（场景重盐稳定性研究，见第三节）

`data/reports/symbolic_planner_*_20260926_0441*/`、`..._044237` 是修协议前的中间产物，保留但不引用。

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
（冻结模拟器在被持有物体的 `at` 字段上是陈旧的）。加守卫前后 428 三个模式的数字完全一致，
即符号搜索没有靠这个漏洞得分。

## 二、State Recovery-428（符号层）

| Mode | Points | Final success | Rate | Mean suffix actions | 配对 vs ROUTED（only SYMBOLIC / only REF / 精确 McNemar p） |
|---|---:|---:|---:|---:|---|
| `BFS_SHORT` | 428 | 321 | 0.7500 | 1.002 | 22 / 9 / 0.0294 |
| `BFS_VALID` | 428 | **428** | **1.0000** | 1.252 | **120 / 0 / 1.50e-36** |
| `BFS_EXEC` | 428 | 321 | 0.7500 | 1.002 | 22 / 9 / 0.0294 |

`BFS_VALID` 另外两个参照臂：vs R2_STATE(299) 129/0 p=2.94e-39，vs R1_FROM_STATE(293) 135/0 p=4.59e-41。
LLM 三臂同框参照：R2_STATE 299（0.6986）、R1_FROM_STATE 293（0.6846）、ROUTED 308（0.7196），
平均修复动作 0.951 / 0.893 / 0.916（相对 P0 最短符号路径的均值开销 −0.051 / −0.110 / −0.086）。

分解（`BFS_VALID` 修好而 ROUTED 修坏的 120 点）：wrong_held_object 98 ＋ grasp_failure 22。
ROUTED 在这 120 点上的首错码：`ARM_NOT_EMPTY` 62、`OBJECT_NOT_HELD` 58——
正是"先释放再重抓"这条符号搜索天然会生成、而提示层没能稳定生成的动作链。
这是 P2（硬释放前置条件）的直接证据。

完整机制（逐点核对 107 个错误持物点）：观测状态里右臂持着错误物体，目标事实只提
`on(red_cube_0, blue_tray_1)`。只按 goal 接受时，搜索给出 `pick → place`（1.336 动作/点，
与 P0 证书给出的 goal-only 最短长度完全一致），但验证器的完整性规则因为"结束时仍有手臂持物"
判整段不可执行（`symbolic_valid` 0/107；`goal_satisfied` 记录为 false 是跳过执行的结果，
不是目标事实本身不成立）。加上"不得以持物结尾"这条接受判据后，搜索自动多生成一步
"把手里那个错误物体放掉"（例：`place(right, blue_block_2 → blue_tray_1)` 再 `pick/place` 目标物体），
107/107 全部转为可执行且最终成功。

这条差异的代价是可以精确量化的：**完整性规则在 107 个点上恰好各多 1 个动作**
（`BFS_VALID` 2.336 vs goal-only 证书 1.336，其余三类扰动 overhead 全为 0），
换来 0/107 → 107/107。反过来看 LLM 三臂的平均后缀动作是 0.951 / 0.893 / 0.916，
**低于** goal-only 证书的最短长度 1.002——即三个 LLM 臂的失败形态是"少做一步"（漏掉释放/重抓链），
不是"多做无用动作"。这与 P2 的动机一致。

`BFS_EXEC` 在 428 上的 107 个 `no_plan_within_cap` 全部落在 wrong_held_object：
错误持物点的正确修复需要"释放＋重抓＋放置"至少 3 个动作，而冻结的 MetaWorld 执行协议
只接受 pick/place 成对或单个 push/press 的非空形态，形态过滤器把这类计划整类拒掉。
即符号搜索在 428 上的瓶颈不是搜索，而是端到端协议的动作形态约束。
`BFS_EXEC` 的另一个约束代价：`nominal_state`（目标本来就已满足、最短长度 0）上它必须给出非空可执行形态，
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

配对（单抽签口径，`BFS_EXEC` 100 vs 各冻结臂）：

| 参照臂 | 参照成功 | only SYMBOLIC | only REF | 精确 McNemar p |
|---|---:|---:|---:|---:|
| ROUTED | 98 | 2 | 0 | 0.50 |
| R1_FROM_STATE | 98 | 2 | 0 | 0.50 |
| SELF_REFINE_STATE_V2 | 95 | 5 | 0 | 0.0625 |
| CHECKER_LOOP_STATE_V2 | 71 | 29 | 0 | 3.73e-09 |

ROUTED 这一轮实际花费 104,131 token、100 次模型调用；`BFS_EXEC` 是 0 次调用、0 token。
2/0 的不一致在 100 点、单抽签口径下仍不显著（p=0.5）。

### 3.1 必须先说清的协议混杂：场景是按臂重采样过的

冻结执行器把臂名混进 `stable_seed`（`sha256(task_id, seed, arm, pair_index)`），
所以每个臂是在**各自重新随机化的初始物体摆放**里跑一回合。这意味着：

- 上表的 100 vs 98 **不是**同一场景下的对照，只是"各自的运气"；
- 同一条固定计划换个盐重跑，结果会翻转——428/Fair-100 的所有单抽签端到端数字都带这个噪声。

第一轮实现里三种模式各自用了不同的 `mode` 字符串当盐，得到 98/99/98 且失败点互不重叠，
当时把它读成"接触执行层瓶颈"。改用共享场景盐（`SYMBOLIC_BFS`）重跑后，三个模式都变 100/100，
说明那 2 个失败是场景抽样而不是方法失效。**这一节是本轮实验里最值得写进限制段的东西**：
单抽签的端到端协议在小样本上的区分度低于场景噪声。

### 3.2 场景重盐稳定性研究（去掉这个混杂）

`scripts/reseed_stability_study.py`：对全部 100 个点取符号规划器的 `BFS_EXEC` 计划与
ROUTED 的冻结计划，在同样的 5 个场景盐下各执行一次（两臂同盐 ⇒ 同一初始摆放），
得到 1000 次执行的配对表。零 VLM 调用。

<!-- RESEED_TABLE -->

## 四、能直接写进正文的结论

1. 在给定闭世界符号状态与符号化目标事实的前提下，深度 8 的确定性符号规划器在同一注册动作集合上
   达到 428/428，显著优于 ROUTED 的 308/428（120 个不一致点全部偏向符号搜索，反向 0 个，
   精确 McNemar p=1.5e-36）。
2. 同一符号规划器在 Fair-100 单抽签端到端为 100/100，ROUTED 为 98/98；2 点差异不显著（p=0.5），
   而模型调用与 token 为零。
3. 符号搜索自身暴露的缺口是验证器补上的：只按 goal 接受时，`BFS_SHORT` 在全部 107 个错误持物点上
   产出"以持物结尾"的后缀而被验证器判不可执行（0/107）；加入验证器的完整性规则后
   `BFS_VALID` 在这 107 点上全部通过。即验证层即使对一个完美规划器也是承载性的。
4. 端到端评测的分辨率受场景抽样限制：同一计划换盐会翻转成败（3.1），
   因此任何单抽签的跨臂小差异都不能当证据；本节给出配对重盐协议作为替代口径（3.2）。

## 五、对 4.3 卖点的冲击（按待补清单的风险条款，不淡化）

清单里预写的风险成立，而且比预期更强：符号基线在两个协议的最终成功率上都不低于 ROUTED
（428 符号层 428 vs 308，p=1.5e-36；Fair-100 端到端 100 vs 98，且不占优的部分统计上不可区分）。
因此"LLM 带来更高恢复成功率"这一句在现有两个协议上不能写，4.3 的卖点必须改写为
"可解释性与接口成本"。可写的正面主张收缩为三条，且都需要显式前提：

- 大模型的作用是**接口**：把自然语言指令与感知输出编译成符号搜索所需的闭世界目标事实与状态。
  428 与 Fair-100 都以人工给定的 `goal.facts` 和结构化状态为输入，这一层被协议本身假设掉了。
- 符号搜索需要**可枚举且完备的动作模型**与可信状态；这两条前提一旦不成立（P3 的腐蚀状态、
  未见物体引用），搜索要么无解要么自信地给出错计划。
- 验证器＋能力注册表是**承载性组件**，不是大模型的附属（第 4 节第 3 条由基线自身反证）。

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

# 场景重盐稳定性研究，约 40 分钟（1000 次仿真回合），支持 --resume
$PY scripts/reseed_stability_study.py \
    --symbolic-cases data/reports/symbolic_planner_fair100_<TS>/symbolic_planner_cases.jsonl \
    --salts 5 --output data/reports/reseed_stability_<TS>/cells.jsonl
```
