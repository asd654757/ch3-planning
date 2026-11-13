"""Export proposed chapter-three flow and a source-attributed comparison.

These are design figures, not plots of new experimental results.
Usage: python scripts/draw_chapter3_revised_flow.py --font FONT --output DIR
"""
import argparse
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties, fontManager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

COLORS = {
    "ink": "#20364B", "muted": "#667789", "blue": "#EAF1FA", "blue_edge": "#3E6596",
    "teal": "#E7F3F0", "teal_edge": "#2A7A70", "amber": "#FFF2D9", "amber_edge": "#B68029",
    "gray": "#F0F3F6", "gray_edge": "#8493A2", "green": "#EAF5E8", "green_edge": "#5C8853",
}


class Diagram:
    def __init__(self, size, font):
        self.fig, self.ax = plt.subplots(figsize=size)
        self.fig.subplots_adjust(left=.018, right=.982, top=.99, bottom=.015)
        self.ax.set_xlim(0, 1)
        self.ax.set_ylim(0, 1)
        self.ax.axis("off")
        self.font = font
        self.nodes = {}

    def text(self, x, y, label, size=12, color=None, ha="center", **kwargs):
        return self.ax.text(x, y, label, transform=self.ax.transAxes, fontproperties=self.font,
                            fontsize=size, color=color or COLORS["ink"], ha=ha, va="center", **kwargs)

    def box(self, name, x, y, w, h, label, color="blue", dashed=False, size=12):
        self.nodes[name] = (x, y, w, h)
        self.ax.add_patch(FancyBboxPatch((x-w/2, y-h/2), w, h,
            boxstyle="round,pad=0.006,rounding_size=0.012", transform=self.ax.transAxes,
            facecolor=COLORS[color], edgecolor=COLORS[color+"_edge"], linewidth=1.4,
            linestyle=(0, (5, 3)) if dashed else "solid", zorder=3))
        self.text(x, y, label, size=size, linespacing=1.5, zorder=4)

    def point(self, name, side):
        x, y, w, h = self.nodes[name]
        return {"top": (x, y+h/2+.009), "bottom": (x, y-h/2-.009),
                "left": (x-w/2-.009, y), "right": (x+w/2+.009, y)}[side]

    def arrow(self, points, color=None, dashed=False, width=1.35):
        color = color or COLORS["blue_edge"]
        for a, b in zip(points[:-2], points[1:-1]):
            self.ax.plot([a[0], b[0]], [a[1], b[1]], transform=self.ax.transAxes,
                         color=color, linewidth=width, linestyle="--" if dashed else "-", zorder=2)
        self.ax.add_patch(FancyArrowPatch(points[-2], points[-1], transform=self.ax.transAxes,
            arrowstyle="-|>", mutation_scale=12, linewidth=width, color=color,
            linestyle="--" if dashed else "-", zorder=2))

    def connect(self, a, b, a_side="bottom", b_side="top", **kwargs):
        self.arrow([self.point(a, a_side), self.point(b, b_side)], **kwargs)

    def save(self, output, stem):
        self.fig.savefig(output/f"{stem}.png", dpi=180, facecolor="white")
        # Paths make the SVG self-contained on machines without Chinese fonts.
        with matplotlib.rc_context({"svg.fonttype": "path", "pdf.fonttype": 3}):
            self.fig.savefig(output/f"{stem}.svg", facecolor="white")
            self.fig.savefig(output/f"{stem}.pdf", facecolor="white")
        # Keep a text-editable SVG as an additional authoring source.
        with matplotlib.rc_context({"svg.fonttype": "none"}):
            self.fig.savefig(output/f"{stem}_editable.svg", facecolor="white")
        for svg in output.glob(f"{stem}*.svg"):
            svg.write_text("\n".join(line.rstrip() for line in svg.read_text().splitlines())+"\n")
        plt.close(self.fig)


def main_flow(font, output):
    d = Diagram((12, 12.5), font)
    d.text(.5, .975, "第三章建议主流程：执行证据与历史约束下的任务规划及恢复", 18)
    d.text(.5, .943, "ROUTED 优化设计稿｜保留现有执行链，集成任务修订原型；性能优势仍需对照验证", 10.5, COLORS["muted"])
    d.box("task", .28, .884, .34, .061, "自然语言任务 / 用户新指令", "blue", size=12.5)
    d.box("obs", .72, .884, .34, .061, "RGB、机器人状态与执行回执", "gray", size=12.5)
    labels = [
        ("semantics", .785, "① LLM 语义解析与修订候选\n程序管理任务版本及生效范围", "teal", True),
        ("state", .677, "② 当前状态与执行历史维护\n区分有证据支持、未知与反证", "blue", False),
        ("check", .569, "③ 修订影响与历史一致性判定\n保留未声明修改项，检查有界可完成性", "teal", True),
        ("repair", .461, "④ ROUTED：剩余任务恢复\n有效后缀复用；否则 LLM 局部修复\n必要时从当前状态重规划（预算内）", "blue", False),
        ("validate", .353, "⑤ 多层校验与执行绑定\n对象、技能、状态、目标及版本", "blue", False),
        ("execute", .245, "⑥ 编译后调用固定执行器\n执行单步技能，不依据预测宣告成功", "gray", False),
        ("confirm", .137, "⑦ 执行后证据确认\n核对实际效果，判断当前任务是否完成", "blue", False),
    ]
    for name, y, label, color, dashed in labels:
        d.box(name, .49, y, .39, .072, label, color, dashed, size=11.2 if name == "repair" else 12)
    for a, b in zip([x[0] for x in labels[:-1]], [x[0] for x in labels[1:]]):
        d.connect(a, b)
    d.arrow([d.point("task", "bottom"), (.28, .785), d.point("semantics", "left")])
    d.arrow([d.point("obs", "bottom"), (.72, .677), d.point("state", "right")], color=COLORS["gray_edge"])
    d.text(.575, .515, "可完成", 10, COLORS["teal_edge"])
    d.text(.575, .299, "通过", 10, COLORS["blue_edge"])
    d.box("query", .85, .621, .23, .084, "证据不足\n相关证据查询 / 旧记录核查", "amber", True, size=11)
    d.box("conflict", .85, .452, .23, .084, "历史或要求冲突\n暂停，给出冲突与修订选项", "teal", True, size=11)
    d.arrow([d.point("check", "right"), d.point("query", "left")], color=COLORS["amber_edge"])
    d.arrow([d.point("check", "right"), d.point("conflict", "left")], color=COLORS["teal_edge"])
    d.arrow([d.point("query", "right"), (.983, .621), (.983, .719), (.72, .719), (.72, .677), d.point("state", "right")], color=COLORS["amber_edge"])
    d.text(.847, .736, "能补齐：更新证据；否则暂停澄清", 9.3, COLORS["amber_edge"])
    d.arrow([d.point("conflict", "right"), (.994, .452), (.994, .785), d.point("semantics", "right")], color=COLORS["teal_edge"], dashed=True)
    d.text(.85, .805, "仅在获得明确新指令后修订", 9.3, COLORS["teal_edge"])
    d.box("caps", .144, .452, .20, .11, "能力注册表\n调用、观测与控制预算\n作为全流程只读约束", "gray", size=10.5)
    d.arrow([d.point("caps", "right"), d.point("repair", "left")], color=COLORS["gray_edge"], dashed=True, width=1.1)
    d.arrow([d.point("caps", "right"), (.26, .452), (.26, .353), d.point("validate", "left")], color=COLORS["gray_edge"], dashed=True, width=1.1)
    d.arrow([d.point("validate", "left"), (.278, .353), (.278, .461), d.point("repair", "left")], color=COLORS["blue_edge"])
    d.text(.262, .405, "拒绝后修复", 9.2, COLORS["blue_edge"], rotation=90)
    d.arrow([d.point("confirm", "left"), (.024, .137), (.024, .677), d.point("state", "left")])
    d.text(.012, .405, "未完成 / 失败 / 未知：带新证据进入下一轮", 10, COLORS["blue_edge"], rotation=90)
    d.box("done", .847, .137, .232, .072, "有证据满足当前要求\n记录任务完成", "green", size=11.5)
    d.connect("confirm", "done", "right", "left", color=COLORS["green_edge"])
    d.text(.5, .071, "实线框：现有执行链基础　　青色虚线框：已有原型，待主链集成　　黄色虚线框：证据获取接口待补充", 9.3, COLORS["muted"])
    d.text(.5, .042, "不可改写已发生事件；未经授权不改任务要求；预测不写成事实；预算耗尽或无可行恢复时统一暂停并报告。", 9.3, COLORS["ink"])
    d.text(.5, .017, "已实现范围与拟扩展范围见配套说明；有界可完成性和 RGB 证据均不等于开放世界物理正确性保证。", 8.7, COLORS["muted"])
    d.save(output, "chapter3_proposed_main_flow")


def comparison(font, output):
    d = Diagram((14, 11), font)
    d.text(.5, .973, "规划闭环对照：参考论文图 4.2 与 ROUTED 优化设计", 20)
    d.text(.255, .926, "参考论文：场景建模与 PDDL 自优化", 14)
    d.text(.755, .926, "你的第三章：执行证据与历史约束（建议稿）", 14)
    d.ax.plot([.5, .5], [.075, .94], color="#D8E0E8", linewidth=1)
    paper = [
        ("p1", .827, "任务指令＋初始场景图", "gray"),
        ("p2", .713, "LLM：领域生成、场景剪枝、问题生成", "blue"),
        ("p3", .599, "LLM：按动作语义分解子目标", "blue"),
        ("p4", .485, "FastDownward＋LLM：候选计划", "blue"),
        ("p5", .371, "VAL 等验证器：检验候选计划", "teal"),
        ("p6", .257, "有效子计划 → 执行与状态推进", "gray"),
        ("p7", .132, "失败：诊断后修订领域 D 或问题 P", "blue"),
    ]
    ours = [
        ("o1", .827, "任务 / 新指令＋当前观测＋执行历史", "gray"),
        ("o2", .713, "LLM 语义候选＋任务版本＋证据状态", "blue"),
        ("o3", .599, "影响范围＋历史一致性 / 可完成性", "teal"),
        ("o4", .485, "复用有效后缀 / LLM 修复或重规划", "blue"),
        ("o5", .371, "契约校验 → 固定执行器", "blue"),
        ("o6", .257, "执行后确认 → 完成或更新状态", "teal"),
        ("o7", .132, "证据不足 / 冲突：查询、澄清或暂停", "amber"),
    ]
    for name, y, label, color in paper:
        d.box(name, .255, y, .316, .068, label, color, size=11.5)
    for name, y, label, color in ours:
        d.box(name, .755, y, .316, .068, label, color, name in ("o3", "o7"), size=11.5)
    for items in (paper, ours):
        for a, b in zip(items[:5], items[1:6]):
            d.connect(a[0], b[0])
    d.arrow([d.point("p5", "right"), (.468, .371), (.468, .132), d.point("p7", "right")])
    d.text(.454, .237, "失败", 9, COLORS["blue_edge"], rotation=90)
    d.arrow([d.point("p7", "left"), (.05, .132), (.05, .713), d.point("p2", "left")])
    d.text(.034, .42, "修正建模后重新求解", 9.5, COLORS["blue_edge"], rotation=90)
    d.arrow([d.point("p6", "right"), (.443, .257), (.443, .485), d.point("p4", "right")], color=COLORS["teal_edge"], dashed=True)
    d.text(.431, .489, "状态推进 / 下一子目标", 9, COLORS["teal_edge"], rotation=90)
    d.arrow([d.point("o3", "right"), (.974, .599), (.974, .132), d.point("o7", "right")], color=COLORS["amber_edge"])
    d.text(.989, .36, "无法直接继续", 9, COLORS["amber_edge"], rotation=90)
    d.arrow([d.point("o7", "left"), (.55, .132), (.55, .713), d.point("o2", "left")], color=COLORS["amber_edge"])
    d.text(.535, .42, "以新证据或明确新指令更新", 9.5, COLORS["amber_edge"], rotation=90)
    d.arrow([d.point("o6", "right"), (.94, .257), (.94, .713), d.point("o2", "right")], color=COLORS["teal_edge"], dashed=True)
    d.text(.927, .462, "新执行证据", 9.3, COLORS["teal_edge"], rotation=90)
    d.text(.5, .065, "左侧依据易廷昊论文图 4.2 与第 4.2 节简化重绘；右侧为待集成设计，不代表已证实独立创新或性能优势。", 10, COLORS["muted"])
    d.text(.5, .029, "共同点：结构化表示、验证和反馈闭环。比较重点：修订对象、生效范围、历史与事实保持，以及何时不能继续执行。", 10, COLORS["ink"])
    d.save(output, "chapter3_vs_reference_flow")


MAIN_MERMAID = """flowchart TD
    U[任务指令或用户修订] --> C[任务语义与版本管理]
    O[RGB、机器人状态与执行回执] --> S[当前状态与只读执行历史]
    C --> S
    S --> H{修订影响、历史一致性与有界可完成性}
    H -->|可完成| R[ROUTED：复用有效后缀；必要时调用LLM修复或从当前状态重规划]
    H -->|证据不足| Q[相关证据查询或旧记录核查]
    H -->|冲突| P[暂停并报告冲突与可选修订]
    Q -->|得到新证据| S
    Q -->|无法补齐| P
    P -->|得到明确新指令| C
    K[真实能力注册与资源预算] -.只读约束.-> R
    K -.只读约束.-> V
    R --> V{对象、技能、状态、目标与版本校验}
    V -->|不通过且预算允许| R
    V -->|通过| E[编译与固定技能执行]
    E --> F{执行后证据是否支持当前任务完成}
    F -->|是| D[记录完成]
    F -->|未完成、失败或未知| S
    R -->|预算耗尽或无可行恢复| P
"""


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--font", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    fontManager.addfont(str(args.font))
    font = FontProperties(fname=str(args.font))
    main_flow(font, args.output)
    comparison(font, args.output)
    (args.output/"chapter3_proposed_main_flow.mmd").write_text(MAIN_MERMAID)
    (args.output/"figure_manifest.json").write_text(json.dumps({
        "purpose": "proposed_chapter_flow_and_experiment_design_not_new_results",
        "source": str(Path(__file__)), "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "font": str(args.font), "font_source": "https://github.com/notofonts/noto-cjk",
        "reference": "易廷昊博士论文，图4.2；正文55页，PDF70页；对照图左侧为简化重绘",
        "implementation_status": "ROUTED has an execution chain; history-revision prototype is separate; full integration and targeted evidence acquisition remain proposed",
        "new_model_calls": 0, "new_robot_trials": 0,
        "svg": "regular SVG uses glyph paths; *_editable.svg retains text and needs a Chinese font",
    }, ensure_ascii=False, indent=2)+"\n")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
