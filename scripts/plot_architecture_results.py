"""Plot frozen architecture results; do not rerun or modify the experiment."""
from hashlib import sha256
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def main():
    source = Path("data/collections/architecture_loop_20261117_v1")
    out = Path("data/collections/architecture_paper_figures_20261118_v1")
    out.mkdir(parents=True, exist_ok=False)
    summary = json.loads((source / "summary.json").read_text())
    timings = json.loads((source / "timings.json").read_text())
    arms = ("standard", "fixed", "source_partial", "learned")
    labels = ("No failure bank", "Fixed bank", "Source partial\nnogoods", "Full bank")
    values = [summary["methods"][a] for a in arms]
    x = np.arange(len(arms))
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False,
                         "axes.spines.right": False, "svg.fonttype": "none"})
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.0), constrained_layout=True)
    runtime = [v["metrics"]["runtime_backend_calls"] for v in values]
    repair = [v["metrics"]["repair_backend_calls"] for v in values]
    axes[0].bar(x, runtime, .62, label="Runtime", color="#4477aa")
    axes[0].bar(x, repair, .62, bottom=runtime, label="Repair search", color="#66ccee")
    for i, (r, p) in enumerate(zip(runtime, repair)):
        axes[0].text(i, r+p+7, str(r+p), ha="center")
    axes[0].set(ylim=(0, 570), ylabel="Complete-planner calls", title="(a) Same behavior, fewer solver calls")
    axes[0].legend(frameon=False, loc="upper right")
    for i, arm in enumerate(arms):
        seconds = [t["total_ms"]/1000 for t in timings if t["arm"] == arm]
        assert len(seconds) == 3
        axes[1].scatter(np.full(3, i)+np.array([-.07, 0, .07]), seconds,
                        s=34, color="#4477aa", zorder=3)
        med = summary["methods"][arm]["medians"]["total_ms"]/1000
        axes[1].plot([i-.23, i+.23], [med, med], color="#cc6677", lw=2.4,
                     label="Median" if i == 0 else None)
    axes[1].set(ylim=(0, 5.8), ylabel="Full symbolic loop wall time (s)",
                title="(b) Three repeats; initialization included")
    axes[1].legend(frameon=False, loc="upper right")
    for ax in axes:
        ax.set_xticks(x, labels)
        ax.grid(axis="y", alpha=.18)
        ax.set_axisbelow(True)
    fig.suptitle("32 sequences / 384 requests; all four arms complete 223 with identical semantic traces", fontsize=11)
    for ext in ("png", "svg", "pdf"):
        fig.savefig(out/f"architecture_efficiency.{ext}", dpi=200)
    plt.close(fig)
    digest = lambda p: sha256(p.read_bytes()).hexdigest()
    manifest = {
        "inputs": {str(source/n): digest(source/n) for n in ("summary.json", "timings.json", "manifest.json")},
        "script": {str(Path(__file__)): digest(Path(__file__))},
        "artifacts": {p.name: digest(p) for p in out.iterdir() if p.is_file()},
        "physical_execution": False,
    }
    (out/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    print(out)


if __name__ == "__main__":
    main()
