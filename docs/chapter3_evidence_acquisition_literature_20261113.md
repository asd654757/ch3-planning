# 执行不确定性下的任务证据获取：文献、项目与方法启发

检索核对日期：2026-11-13（北京时间）。本轮依据论文原文、作者项目页和作者仓库，不依据聚合站的自动摘要作方法结论。仓库核对到 README、目录和下列指定源码；**没有安装这些系统或复现其论文实验**。本轮无付费模型请求、无新增物理试验。

## 1. 新研究定位

第三章问题调整为：**执行不确定性下，如何获取足够且必要的任务证据？**

直接反馈重规划作为所有系统组共享的后端。拟研究的变量是：哪些未知影响决策、选择什么核查方式、观察到答案后是否继续查、何时足以执行、证据无法获得时如何终止。原有任务版本、执行历史和动作门槛继续作为基础设施；增量编辑格式及局部修复优于重规划不再作为预设主张。

最值得采用的算法起点是 **Decision Region Determination（DRD，决策区域判定）**，工程参考为 VAP-TAMP/TAMPURA，评价参考为 KnowNo/IntroPlan。下面指出的是研究基础和候选差异，不是已确认的新颖性。

## 2. 优先阅读的文献

### A. EC²：先理解“足够知道”，不必识别完整世界

Golovin、Krause、Ray，*Near-Optimal Bayesian Active Learning with Noisy Observations*，NeurIPS 2010。[作者论文](https://www.cs.cmu.edu/afs/.cs.cmu.edu/Web/People/dgolovin/papers/nips10.pdf)、[arXiv](https://arxiv.org/abs/1010.3091)。

把昂贵检查视为测试，只区分会导致不同决策的假设类，按成本自适应选择测试。它为我们的“充分证据”提供已有理论起点。原问题需要先验及观测模型，类之间互斥；机器人可能有多个同时合法动作，不能直接把任意一个规划器输出当作唯一类别。不能移植名称或未核验的理论保证。

### B. DiRECt/BiSECt：与当前问题最贴近的算法和强对照

Choudhury 等，*Near-Optimal Edge Evaluation in Explicit Generalized Binomial Graphs*，2017；*Bayesian Active Edge Evaluation on Expensive Graphs*，IJCAI 2018。[2017 论文](https://arxiv.org/abs/1706.09351)、[2018 正式论文](https://www.ijcai.org/proceedings/2018/0679.pdf)、[作者代码](https://github.com/sanjibanc/matlab_learning_collision_checking)。

核心是少做昂贵碰撞检查，尽快确认一条候选路径有效；候选路径对应可重叠的世界区域。对本章的启发是：核实事实应服务于确认一种可执行决策，不以最大化世界信息量为目标。静态候选库、世界先验和独立性等假设需逐项核对；涉及物理探测、证据失效的动态问题需要另建转移模型。

**它必须作为算法基线。** 若我们只是把路径换成任务后缀、把碰撞检查换成传感器查询，应明确属于方法迁移，不能认领 DRD 思路本身。作者代码是 MATLAB；已读 `direct_drd.m`、`direct_drd_bern.m` 和目标函数。移植需对照论文核验异成本归一化，不能把仓库函数名与论文公式完全等同。

### C. KnowNo：把必要帮助和贸然执行同时衡量

Ren 等，*Robots That Ask For Help: Uncertainty Alignment for Large Language Model Planners*，CoRL 2023。[论文](https://proceedings.mlr.press/v229/ren23a.html)、[项目](https://robot-help.github.io/)、[官方代码](https://github.com/google-research/google-research/tree/master/language_model_uncertainty)。

通过校准后的候选集合决定是否请求帮助，提供“少问且够用”的评价参考。无需微调基础模型不代表无需标注校准数据；原文关于任务完成的保证依赖场景文本落地、执行能力和帮助正确等条件，不能直接变成物理观测可靠性保证。

### D. IntroPlan：避免“问得多就是更安全”的错误结论

Liang 等，*Introspective Planning: Aligning Robots’ Uncertainty with Inherent Task Ambiguity*，NeurIPS 2024。[项目](https://introplan.github.io/)、[正式论文](https://papers.neurips.cc/paper_files/paper/2024/file/8451a20c5a7e0ee5671dda28f7daf7f3-Paper-Conference.pdf)、[代码](https://github.com/kevinliang888/IntroPlan)。

值得借鉴的是 overask、overstep、合规与不安全候选等分项评价。其语义推理/检索和校准方案面向指令歧义，不能直接当作执行状态核查器。已读 `metrics.py`、`cp_utils.py`；指标含特定任务规则，借用定义时需重新给出本章分母。候选 token 分数也不能直接视为传感器真实性概率。

### E. TAMPURA：查询与物理动作应放在共同代价下考虑

Curtis 等，*Partially Observable Task and Motion Planning with Uncertainty and Risk Awareness*，RSS 2024。[论文](https://arxiv.org/abs/2403.10454)、[项目](https://aidan-curtis.github.io/tampura.github.io/)、[规划代码](https://github.com/aidan-curtis/tampura)、[环境代码](https://github.com/aidan-curtis/tampura_environments)。

以抽象 belief 和不确定动作效果联合考虑信息获取、风险及执行。启发是核查动作本身有前置条件、成本和状态影响。原系统通过采样学习抽象效果模型，不能描述成直接可用的零学习模块。我们宜借接口与问题建模，先用现有有限域精确参考，暂不迁移整套环境。

### F. VAP-TAMP：当前最直接的系统相关工作

Oloo 等，*Robot Planning and Situation Handling with Active Perception*，2026；作者项目页标注 IROS 2026。[论文](https://arxiv.org/abs/2604.26988)、[项目](https://vap-tamp.github.io/vap-tamp/)、[作者仓库](https://github.com/aoloo-r/VAP-TAMP)。

围绕动作前置条件与效果核查谓词，不确定时改变视角，再更新状态并重规划。它已经覆盖“缺证据→主动观测→恢复”，因此我们的流程本身不足以构成创新。其视角选择和观测效率实验值得参考；同图改写问句的一致性不等于独立或校准的物理证据。

已读 `vlm-tamp/active_perception.py` 部分关键实现：低成本头部调整后再尝试底盘视角，存在文本式 confidence 判定。代码入口与论文算法不完全同义，不称完整复现。我们的核查结果应保留 unknown；该基础拒绝机制已有，不能再包装为新算法。

### G. I Won’t Know Until I Try：核查也会改变现场

Yuan、Holladay，*I Won’t Know Until I Try: Interactive Information Gathering for Long-Horizon Manipulation*，2026-10-25 预印本，所查版本标注 under review。[论文](https://arxiv.org/abs/2610.33959)、[全文](https://arxiv.org/html/2610.33959v1)。

推一下判断可移动性、试写判断有无墨水等交互同时揭示属性并改变物理状态。应借鉴 CheckedX 与属性值分开，以及核查后重新观测现场。原设定假设交互结果确定地解析属性；本章可以检验不确定/不可获得结果及费用，但这些差异尚不自动构成创新。此次未从作者论文/页面确认可用的专属代码，不列为已验证开源项目。

### H. CURE：区分不确定来源，借评价曲线，暂不移植模型

Yin 等，*Towards Reliable LLM-based Robot Planning via Combined Uncertainty Estimation*，2025。[论文](https://arxiv.org/abs/2510.08044)、[代码](https://github.com/Firesuiry/CURE)。

分解任务清晰度、熟悉度和预期执行成功等因素；使用 LLM 特征、RND 和回归头。适合参考“成功率—帮助率”评价及不同不确定来源，不适合作为当前无需新增训练的首个实现。其帮助曲线假设帮助能补救任务，我们的档案检索可能无答案，不能直接套用满帮助必达 100% 的解释。

## 3. 项目核查与复用优先级

以下均核对真实仓库内容，未安装或跑通。文件 SHA、链接及读取范围保存在 [项目核查清单](../results/evidence_research_20261113/repository_audit.json)。

| 优先级 | 项目 | 已核对入口 | 建议复用什么 | 本轮判断 |
| --- | --- | --- | --- | --- |
| 1 | [作者 DRD/BiSECt](https://github.com/sanjibanc/matlab_learning_collision_checking) | README、两个核心算法及目标函数、LICENSE | 查询目标、停止条件、标准算法参考 | 按论文独立实现小域对照；不是新算法 |
| 2 | [IntroPlan](https://github.com/kevinliang888/IntroPlan) | README、metrics.py、cp_utils.py | 过问/越权指标及分母定义 | 借指标，不能直接复制场景判分规则 |
| 3 | [KnowNo](https://github.com/google-research/google-research/tree/master/language_model_uncertainty) | README、三份 notebook 的目录 | 校准/测试划分、帮助策略参考 | notebook 使用历史模型接口，需适配后才可复现 |
| 4 | [VAP-TAMP](https://github.com/aoloo-r/VAP-TAMP) | README、主动感知模块关键片段 | 谓词核查、不同成本视角的动作接口 | 依赖真实机器人/地图，先借接口 |
| 5 | [TAMPURA](https://github.com/aidan-curtis/tampura) | README、environment.py、policy 部分、pyproject | belief/observation/执行分离 | Python 3.11，与现有 3.10 环境分开；暂不安装 |
| 6 | [TAMPURA environments](https://github.com/aidan-curtis/tampura_environments) | README | 多类不确定任务构造 | 借任务结构，不迁移整套平台 |
| 7 | [CURE](https://github.com/Firesuiry/CURE) | readme.md、根目录 | 成功—帮助曲线、来源分解 | 学习型辅助模块，非本轮实现主线 |

## 4. 对现有代码的具体诊断

1. `minimal_evidence_query` **已经是任务相关查询**，不是一律询问全部未知；它最小化一个固定历史事件集合的数量，要求全部真假回答分支不再 need_observation。新工作应从这里出发，不能重新宣称首次实现相关性核查。
2. 该查询器不建模证据可获取性、费用、响应 unknown、传感误差或查询执行。最小事件数与最小实际核查成本不同；固定集合与逐次回答后自适应终止不同。
3. `State` 要求两个物体位置和手状态完整，未知主要进入历史事件，且未知事件最多 6 个；当前状态的不确定性无法直接装进该表示。新接口应另建 belief/evidence 层，不把未知偷偷填成 table/empty。
4. `decide` 的 ready 要求所有历史分支共享一条完整后缀。新研究可考察共同下一动作与后续条件策略，但“各世界分别可完成”不足以证明存在可执行的共同策略。
5. 九月份已经有缺失项驱动补问、单请求核查、多视角持物证据，十一月份有有界核查 fixture。缺的是多种真正不同的核查方式及其成本/结果模型，不能仅把同一 observe 延长为多步就测试前瞻算法优势。
6. 历史事件不可由当前照片重建；确认旧事件需查对应档案。用户改变要求与确认物理事实不同，本轮不把“让用户取消要求”当免费证据获取。

本地依据：[历史诊断代码](../ch3/revision/diagnosis.py)、[历史模型](../ch3/revision/history.py)、[九月补问记录](observation_query_progress_20260930.md)、[单请求记录](single_observation_query_progress_20260930.md)、[最新开发结果](revision_recovery_results_20261113.md)。

## 5. 由文献得到的研究假设

建议进一步聚焦：**在成本、可获取性与时效约束下，为下一项任务决策获取充分证据。**

这里的候选差异，是在一个执行模型内联合处理：当前物理事实与历史事件的不同证据渠道、多种核查方式的真实成本和状态影响、无结论的结果，以及多种同时合法动作。不是把这些已有概念各自当创新点。若最终可直接化为标准 DRD 或小型 POMDP，应如实归因；需要在标准强对照下找到额外机制或明确的系统贡献。

已做 [六个枚举反例](../results/evidence_research_20261113/design_counterexamples.json) 检查定义：世界信息增益会偏向无关事实；最少查询数不等于最低费用；自适应查询可早停；多个世界选出的计划不同仍可能有共同合法动作；两两动作交集非空不保证全局交集；无档案或 unknown 不能当真值。它们是人工小例和标准精确决策树参考，**不证明我们的算法优于已有方法，不计模型或机器人实验样本**。

建议实施顺序和公平对照见 [新方向实施方案](chapter3_evidence_acquisition_plan_20261113.md)。
