# P2 反事实：强制"释放"前置条件能白拿多少（零 token、零 VLM、冻结评分器）

产物目录：`data/reports/release_counterfactual_428_20260926/`
（`release_counterfactual_cases.jsonl` 1284 行 ＋ `summary.json` ＋ `analysis.md` ＋
`residual_taxonomy_{ROUTED,R2_STATE,R1_FROM_STATE}.json`）；运行日志
`data/reports/logs/release_counterfactual.log`。脚本 `scripts/release_precondition_counterfactual.py`
与 `scripts/release_counterfactual_residuals.py`。输入是只读的
`data/collections/state_recovery_formal_v1_20260914_101157.jsonl`（428 点 × 3 臂），
没有任何 frozen 产物被改写。

## 〇、先给校准，再看表

重打分器（把改写后的后缀喂给冻结的四层 Validator ＋ Goal Checker，评分口径与 428 头条完全相同：
只评后缀、从 `observed_state_facts` 起、不重放已锁定的前缀、空后缀合法）复现冻结
`recovery_success`：**1284/1284 = 1.0000，不一致清单为空**（分臂各 428/428，见
`summary.json` 的 `scorer_vs_frozen_success_agreement.disagreements = []`）。
这一行是下面全部数字的前提：如果它不是 1.0000，下表就没有意义。

## 一、规则（一句话）

观测态说某手臂 `arm` 持着 X，而计划在该手臂上的下一个动作不是 `place(X, ...)`，
就在那个动作前面插入 `place(arm, X → table)`。如果计划之后再也不碰这只手臂，则把释放
追加到末尾（`dangling_hold`；这类点全部不改变成败）。除此之外不改动、不重排、不重新搜索任何动作，
也不调用任何模型。

动机来自 P1 的 120 点分解（`symbolic_planner_baseline_findings_20260926.md` 第一节）：
符号搜索 428/428、ROUTED 308/428，120 个不一致点全是 `wrong_held_object`(98) ＋ `grasp_failure`(22)，
113/120 只发了 1 个动作，首错码 `ARM_NOT_EMPTY` / `OBJECT_NOT_HELD`——
读法就是"模型当手是空的"。这条规则把那句假设直接翻译成可执行的反事实。

## 二、主表

| 臂 | 点数 | before | after | Δ | 插入释放的点 | 修好 | 改坏 | 精确 McNemar p | 均值动作 before→after | 冻结形态可执行 before→after |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| ROUTED | 428 | 308 (0.7196) | **347 (0.8107)** | +39 | 92 | 39 | **0** | 3.638e-12 | 0.916→1.131 | 231→192 |
| R2_STATE | 428 | 299 (0.6986) | 335 (0.7827) | +36 | 103 | 36 | **0** | 2.910e-11 | 0.951→1.192 | 225→215 |
| R1_FROM_STATE | 428 | 293 (0.6846) | 332 (0.7757) | +39 | 93 | 39 | **0** | 3.638e-12 | 0.893→1.110 | 219→180 |

三条臂零回退、p 都在 1e-11 量级。**ROUTED 从 0.7196 抬到 0.8107，用的是一句前置条件，不是一分钱 token。**

收益的来源非常集中：

- 插入只发生在 `wrong_held_object` 上（ROUTED 92/107 点），其余三种扰动 0/107 触发——
  规则以 `holding(...)` 事实为条件，而该扰动正是"手里多了个没预期的东西"。
- ROUTED 修好的 39 点全部是 `wrong_held_object`；冻结首错码 `ARM_NOT_EMPTY` 37 ＋ `OBJECT_NOT_HELD` 2；
  按任务族 `press` 35 / `pick_place` 2 / `push` 2；按种子 14/13/12 三个种子均匀分布（不是单抽签现象）。
- 代价恰好是每点 ＋1 动作：39 个点的合并动作数 1.10→2.10，全样本均值动作 0.916→1.131（＋0.215）。
- 典型修好的计划（`multiskill_press_024/0/wrong_held_object`）：
  before `press(red_button_36, left)` → after `place(yellow_button_39→table, left); press(red_button_36, left)`。

## 三、最重要的一条限制：这些点在冻结的端到端协议里兑不了现

39 个修好的点**全部**在"单集可执行形态"白名单（`is_sim_executable`，
镜像 `sim_compare_baselines.is_executable_multiskill`：只允许 pick→place 成对且同物、
单个 push、单个 press）之外：形态可执行 231→192，**损失集合与修好集合精确相同，形态净增 0**。
也就是：评分器层面白拿的 +39，在闭环执行层面要拿到必须先给执行器加一个
release/drop 原语（或把"释放"写成 pick/place 对的合法形态）。这是一个**协议成本**，不是评分器假象——
同一套形态判据也解释了为什么单次生成的动作数上限把计划压成 1 个动作（第二节动机）。

R2_STATE 另有 26 个形态标志由 False 翻 True（`pick(X)` 末尾追加 `place(X→table)` 正好凑成对）。
这 26 点**一个都没修好**，是形态判据不看 `arm` 字段造成的记账现象，写进限制段，不写进结果段。

## 四、残余 81 点不是一个缺陷，是五类（ROUTED）

| 缺陷类 | 点 | 扰动 | 任务族 | 首错码 | 含义 |
|---|---:|---|---|---|---|
| `release_present_but_plan_still_rejected` | 42 | wrong_held_object 42 | pick_place 34 / push 8 | OBJECT_NOT_HELD 34 / SCHEMA_ERROR 8 | 释放做完了，计划仍然不去重新抓目标物——缺的是**重新规划**，不是前置条件 |
| `other_OBJECT_NOT_HELD` | 22 | grasp_failure 22 | pick_place 22 | OBJECT_NOT_HELD 22 | 手里根本没有东西可释放（抓取失败），**任何前置条件规则都够不到** |
| `wrong_skill_family` | 11 | wrong_held_object 11 | push 11 | SCHEMA_ERROR 11 | 技能族选错（该 push 的地方发 place/pick 组合） |
| `push_or_press_own_picked_object` | 5 | wrong_held_object 5 | push 5 | ARM_NOT_EMPTY 5 | 规则要求先释放，但被释放的正是它随后要 push 的物体；`multiskill_push_019/0` 手工核对：`place→pick→push` 在第 3 步被拒 `right 非空，不能 push` |
| `released_but_task_still_undone` | 1 | wrong_held_object 1 | push 1 | NONE | 计划通过校验但目标事实未成立 |

合计 22＋42＋11＋5＋1 = 81 ✓（残余 81 点的形态可执行数 0/81）。
R2_STATE 残余 93、R1_FROM_STATE 残余 96，分类见各自 `residual_taxonomy_*.json`。

## 五、与 P1 的账：39 / 120

| 口径 | 成功率 | 相对符号搜索 428/428 的差距 |
|---|---|---:|
| ROUTED 冻结原样 | 308/428 = 0.7196 | 120 |
| ROUTED ＋强制释放前置 | 347/428 = 0.8107 | **81** |
| 符号 BFS（`BFS_VALID`） | 428/428 = 1.0000 | 0 |

一句前置条件收掉 120 点差距的 **32.5%（39/120）**，零 token、零回退；
剩下 67.5% 的性质是"释放完之后还不知道下一步该干什么"，那正是搜索免费提供的东西（P1：1284 次搜索总耗时 0.57 s）。
这构成 4.3 的一个可量化对照：**修前置条件是提示词层面一句话，修重新规划要换一个推理机制。**

## 六、能写 / 不能写

可以写：
1. 缺失的释放前置条件是**一类可识别、可零成本修复**的失败：一句规则修好 ROUTED 39/428 配对点
   （三臂合计 114/1284）、0 回退、p=3.6e-12。
2. 该修复的收益**全部落在 `wrong_held_object`**，且 `grasp_failure` 一类前置条件在原理上够不到（手里没东西）。
3. 补完之后残余失败**分五类**，主要残余是"不重新抓目标物"，即重新规划缺失。
4. 端到端兑现还需要执行器层支持（release/drop 原语），否则 +39 停在评分器里。

不能写（会被数据反驳）：
- "前置条件是 ROUTED 与符号搜索之间差距的主因"——只有 1/3。
- "加了释放规则就能达到符号层水平"——0.8107 ≪ 1.0000。
- 任何"这些点在闭环里也能收益"的暗示——形态判据 39/39 拒绝。

**不淡化条款仍然生效**：4.3 的卖点是"可解释性与接口成本"，本条反事实只是把接口成本量化成
"一句话前置条件＝＋39 点＝0 token"，不改变 4.3 的结论，也不回头修改任何 05 章统计量。

## 七、下一步（B：证据门控修复，头号指标是静默失败率）

P3 已经量到"相信态 vs 真实态"的落差里最危险的不是失败而是**自信地错**
（`goal_fact_added`：confidently_wrong 1.0000、空计划 1.0000；`holding_hidden` 0.3271）。
本节的五类残余正好给出门控规则该长什么样：`OBJECT_NOT_HELD` / `ARM_NOT_EMPTY` 这类
"计划与观测态冲突"是可以零成本自检的（就是第二节的规则），而 `released_but_task_still_undone`
和 `grasp_failure` 类必须重新规划。顺带本节给出一个可证伪的说法：
**STM/LTM 记忆增强修不了缺前置条件这类错**——39 个修好的点里没有一个需要跨集记忆，
只需要当前状态的一句前置检查；反之 `grasp_failure` 的 22 点无论记多少历史都够不到。
（对照 PragmaBot：它的增益来自反思与检索，两者都以"能观测到失败"为前提，
而 P3 的静默失败恰恰是系统自报成功。）

## 八、复现

```bash
cd /root/autodl-tmp/ch3-planning
export PYTHONPATH=. MUJOCO_GL=egl
PY=/root/autodl-tmp/.venvs/metaworld-lerobot/bin/python

# 反事实重打分（零 VLM、零 token）
$PY scripts/release_precondition_counterfactual.py \
  --output-dir data/reports/release_counterfactual_428_20260926

# 残余失败分解（一次一臂，三次运行）
for B in ROUTED R2_STATE R1_FROM_STATE; do
  $PY scripts/release_counterfactual_residuals.py \
    --cases data/reports/release_counterfactual_428_20260926/release_counterfactual_cases.jsonl \
    --baseline $B \
    --output data/reports/release_counterfactual_428_20260926/residual_taxonomy_$B.json
done
```

两次运行逐字段可比：改写规则只用观测态事实与冻结后缀，不引入任何随机性（与 P3 修掉的
`set(arms)` 哈希平局无关，本脚本不做搜索）。
