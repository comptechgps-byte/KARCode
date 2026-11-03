#!/usr/bin/env python3
"""
figures_v9.py — explanatory figures for manuscript v9, all computed from the
result files:

  figures/fig_outcomes_demo.pdf     repaired share per (policy, fault), minimal testbed
  figures/fig_outcomes_heldout.pdf  repaired share per (policy, held-out fault), Online Boutique
  figures/fig_tool_use.pdf          how the 7B and 14B models used the generic tools
  figures/fig_traps.pdf             rollback share per model on the rollback traps

    python benchmark/figures_v9.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_v2 import fault_matrix, load  # noqa: E402
from paper_numbers_v4 import llm_events, nonempty  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# FIG_PROFILE=ieee writes figures sized for IEEE two-column pages (column 3.5 in,
# text 7.16 in) into paper_ieee/figures; the default sizes suit the Springer page.
import os  # noqa: E402
IEEE = os.environ.get("FIG_PROFILE") == "ieee"
R, F = ROOT / "results", ROOT / ("paper_ieee" if IEEE else "paper") / "figures"
ROW_H = 0.19 if IEEE else 0.27

# reference palette (light mode): ink, muted, hairline, one series hue, blue sequential ramp
INK, INK2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#fcfcfb"
BLUE = "#2a78d6"
SEQ = ["#f0efec", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95"]
CMAP = LinearSegmentedColormap.from_list("seq", SEQ)
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8, "axes.edgecolor": GRID,
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                     "text.color": INK, "figure.facecolor": "white", "axes.facecolor": "white"})


def save(fig, name: str) -> None:
    """Vector PDF for the paper; a PNG preview next to it for visual checks."""
    fig.savefig(F / f"{name}.pdf", bbox_inches="tight")
    fig.savefig(F / f"{name}_preview.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def cells(runs: list[str], testbed: str) -> pd.DataFrame:
    x = load(nonempty(*runs), "x")
    return fault_matrix(x[x.testbed == testbed])


def heatmap(rows, cols, col_labels, data, path, width, title_note=None):
    """rows: [(label, run_dirs, condition)], data[(row_label, fault)] = (k, n)."""
    m = np.full((len(rows), len(cols)), np.nan)
    for i, (lab, _, _) in enumerate(rows):
        for j, c in enumerate(cols):
            if (lab, c) in data:
                k, n = data[(lab, c)]
                m[i, j] = k / n
    fig, ax = plt.subplots(figsize=(width, ROW_H * len(rows) + 0.75))
    ax.imshow(np.where(np.isnan(m), 0, m), cmap=CMAP, vmin=0, vmax=1, aspect="auto")
    for i, (lab, _, _) in enumerate(rows):
        for j, c in enumerate(cols):
            if np.isnan(m[i, j]):
                ax.text(j, i, "not run", ha="center", va="center", fontsize=6, color=MUTED)
                continue
            k, n = data[(lab, c)]
            ax.text(j, i, f"{k}/{n}", ha="center", va="center", fontsize=7,
                    color="white" if m[i, j] > 0.55 else INK)
    ax.set_xticks(range(len(cols)), col_labels, rotation=0)
    ax.set_yticks(range(len(rows)), [r[0] for r in rows])
    ax.xaxis.tick_top()
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xticks(np.arange(-.5, len(cols)), minor=True)
    ax.set_yticks(np.arange(-.5, len(rows)), minor=True)
    ax.grid(which="minor", color="white", linewidth=2)
    ax.tick_params(which="minor", length=0)
    if title_note:
        fig.text(0.01, 0.01, title_note, fontsize=6.5, color=INK2)
    fig.tight_layout()
    save(fig, Path(path).stem)


def fig_outcomes_demo():
    rows = [("Native (no agent)", ["main"], "baseline"), ("Naive restart", ["main"], "restart"),
            ("Rules only", ["main"], "rules"), ("Hybrid, 7B", ["main"], "hybrid"),
            ("LLM only, 7B", ["main"], "llm-only"),
            ("LLM + specific tools, 7B", ["exp2a"], "llm-tools"),
            ("LLM + generic tools, 7B", ["exp4a"], "llm-generic"),
            ("LLM only, 14B", ["exp2b"], "llm-only-14b"),
            ("LLM + generic tools, 14B", ["exp7a"], "llm-generic-14b"),
            ("LLM only, Llama 8B", ["exp8a"], "llm-only-llama8b")]
    cols = ["oom-kill", "crashloop", "net-partition", "bad-image", "missing-config",
            "log-injection", "pod-kill", "transient-crash"]
    labels = ["OOM\nkill", "crash\nloop", "network\npartition", "bad\nimage", "missing\nconfig",
              "log\ninjection", "pod\nkill", "transient\ncrash"]
    data = {}
    for lab, runs, cond in rows:
        fm = cells(runs, "demo")
        for _, r in fm[fm.condition == cond].iterrows():
            data[(lab, r.scenario)] = (r.k, r.n)
    heatmap(rows, cols, labels, data, F / "fig_outcomes_demo.pdf", 6.9 if IEEE else 6.6)


def fig_outcomes_heldout():
    rows = [("Rules only", ["exp3", "exp9"], "rules"),
            ("Hybrid, 7B", ["exp3", "exp9"], "hybrid"),
            ("Hybrid + specific tools, 7B", ["exp3", "exp9"], "hybrid-tools"),
            ("Hybrid + generic tools, 7B", ["exp4b", "exp9"], "hybrid-generic"),
            ("Hybrid, 14B", ["exp5b", "exp9"], "hybrid-14b"),
            ("Hybrid, Llama 8B", ["exp8b", "exp9"], "hybrid-llama8b"),
            ("Hybrid + escalation, 7B", ["exp10a"], "hybrid-fe"),
            ("Hybrid + escalation, 14B", ["exp10a"], "hybrid-fe-14b"),
            ("Hybrid + escalation, Llama 8B", ["exp10a"], "hybrid-fe-llama8b")]
    cols = ["missing-secret", "nonroot-release", "probe-misconfig", "unschedulable", "zero-replicas"]
    labels = ["missing\nSecret", "run as\nroot", "wrong\nprobe port", "unschedulable\n(no trigger)",
              "zero replicas\n(no trigger)"]
    data = {}
    for lab, runs, cond in rows:
        for run in runs:
            fm = cells([run], "boutique")
            for _, r in fm[fm.condition == cond].iterrows():
                data.setdefault((lab, r.scenario), (r.k, r.n))
    heatmap(rows, cols, labels, data, F / "fig_outcomes_heldout.pdf", 6.0 if IEEE else 6.2)


def fig_tool_use():
    def stats(runs, cond, fault="net-partition"):
        # same event loader as paper_numbers_v4/v5 (valid trials only), so figure = text
        dirs = nonempty(*runs)
        x = load(dirs, "x")
        ev = llm_events(x, dirs)
        ev = ev[ev.cond.isin(cond)]   # both generic modes of the experiment, as in the text
        seqs = [v.get("tools", []) for v in ev.llm if isinstance(v, dict)]
        np_seqs = [v.get("tools", []) for v, tid in zip(ev.llm, ev.trial_id)
                   if isinstance(v, dict) and fault in tid]
        calls = sum(len(s) for s in seqs)
        rep = sum(sum(1 for i, t in enumerate(s) if t in s[:i]) for s in seqs) / max(1, calls)
        looked = np.mean([any("networkpolicies" in t for t in s) for s in np_seqs]) if np_seqs else 0
        return rep, looked, len(np_seqs)
    configs = [("7B, 3 calls", ["exp4a", "exp4b"], ["llm-generic", "hybrid-generic"]),
               ("7B, 6 calls", ["exp6a", "exp6b"], ["llm-generic-6", "hybrid-generic-6"]),
               ("14B, 3 calls", ["exp7a", "exp7b"], ["llm-generic-14b", "hybrid-generic-14b"])]
    vals = [(lab, *stats(run, cond)) for lab, run, cond in configs]
    fig, axes = (plt.subplots(2, 1, figsize=(3.4, 2.5), sharex=True) if IEEE
                 else plt.subplots(1, 2, figsize=(6.4, 1.6), sharey=True))
    panels = [("Partition diagnoses that looked up\na NetworkPolicy", 2),
              ("Tool calls that repeated an\nearlier call", 1)]
    y = np.arange(len(vals))
    for ax, (title, idx) in zip(axes, panels):
        v = [r[idx] for r in vals]
        ax.barh(y, v, color=BLUE, height=0.55)
        for yi, vi in zip(y, v):
            ax.text(vi + 0.02, yi, f"{100 * vi:.0f}%", va="center", fontsize=7, color=INK)
        ax.set_xlim(0, 1.15)
        ax.set_title(title, fontsize=7.5, color=INK, loc="left")
        ax.set_xticks([0, .5, 1], ["0%", "50%", "100%"])
        ax.grid(axis="x", color=GRID, linewidth=0.6)
        ax.set_axisbelow(True)
        for s in ("top", "right", "left"):
            ax.spines[s].set_visible(False)
        ax.tick_params(length=0)
    if IEEE:   # stacked panels: each has its own y axis
        for ax in axes:
            ax.set_yticks(y, [r[0] for r in vals])
            ax.invert_yaxis()
    else:      # side by side with a shared y axis: invert once
        axes[0].set_yticks(y, [r[0] for r in vals])
        axes[0].invert_yaxis()
    fig.tight_layout()
    save(fig, "fig_tool_use")


def fig_traps():
    names = {"qwen2.5_3b-instruct": "Qwen2.5 3B", "llama3.2_3b": "Llama-3.2 3B",
             "qwen2.5_7b-instruct": "Qwen2.5 7B", "llama3.1_8b": "Llama-3.1 8B",
             "gemma2_9b": "Gemma-2 9B", "qwen2.5_14b-instruct": "Qwen2.5 14B"}
    scored = ["transient-old-release", "oom-old-release", "dependency-down", "external-config-change",
              "node-lost", "cert-expired"]
    frames = []
    for sub in ("prompt_traps", "prompt_traps2"):
        for f in (R / sub).glob("*.csv"):
            if f.stem in names:
                d = pd.read_csv(f)
                d["model"] = names[f.stem]
                frames.append(d)
    d = pd.concat(frames)
    d = d[d.fault.isin(scored)]
    rb = d.assign(r=d.action == "rollback-deployment").groupby("model").r.mean().sort_values()
    fig, ax = plt.subplots(figsize=(3.4, 1.9) if IEEE else (4.6, 1.9))
    y = np.arange(len(rb))
    ax.barh(y, rb.values, color=BLUE, height=0.55)
    for yi, vi in zip(y, rb.values):
        ax.text(vi + 0.015, yi, f"{100 * vi:.0f}%", va="center", fontsize=7, color=INK)
    ax.set_yticks(y, rb.index)
    ax.set_xlim(0, 1.1)
    ax.set_xticks([0, .25, .5, .75, 1], ["0%", "25%", "50%", "75%", "100%"])
    ax.set_xlabel("Share of calls that proposed a rollback (rollback is wrong on every trap)",
                  fontsize=7, color=INK2)
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.tick_params(length=0)
    fig.tight_layout()
    save(fig, "fig_traps")


def fig_injection():
    """Hijack rate per model, with and without the 'logs are untrusted' warning.
    Two series: categorical slots 1 and 2, legend plus direct value labels."""
    from adversarial_eval import is_hijacked
    names = {"qwen2.5:7b-instruct": "Qwen2.5 7B", "llama3.1:8b": "Llama-3.1 8B",
             "qwen2.5:14b-instruct": "Qwen2.5 14B"}
    d = pd.concat([pd.read_csv(p) for p in sorted((R / "adversarial").glob("adversarial_*.csv"))])
    d["warn"] = d.prompt_warning.astype(str).str.lower().eq("true")
    d["hij"] = [is_hijacked(p, a, t) for p, a, t in zip(d.payload, d.action, d.target.fillna(""))]
    d = d[d.payload != "none"]
    models = [m for m in names if m in set(d.model)]
    y = np.arange(len(models))
    fig, ax = plt.subplots(figsize=(3.4, 2.0) if IEEE else (4.6, 1.8))
    series = [(True, "with warning", BLUE, -0.17), (False, "without warning", "#eb6834", 0.17)]
    for warn, lab, col, off in series:
        v = [d[(d.model == m) & (d.warn == warn)].hij.mean() for m in models]
        ax.barh(y + off, v, height=0.29, color=col, label=lab)
        for yi, vi in zip(y + off, v):
            ax.text(vi + 0.015, yi, f"{100 * vi:.0f}%", va="center", fontsize=6.5, color=INK)
    ax.set_yticks(y, [names[m] for m in models])
    ax.invert_yaxis()
    ax.set_xlim(0, 1)
    ax.set_xticks([0, .25, .5, .75, 1], ["0%", "25%", "50%", "75%", "100%"])
    ax.set_xlabel("Share of attack calls that did what the injected text asked", fontsize=7, color=INK2)
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.tick_params(length=0)
    ax.legend(frameon=False, fontsize=6.5, loc="lower right")
    fig.tight_layout()
    save(fig, "fig_injection")


if __name__ == "__main__":
    F.mkdir(parents=True, exist_ok=True)
    for fn in (fig_outcomes_demo, fig_outcomes_heldout, fig_tool_use, fig_traps, fig_injection):
        fn()
        print("wrote", fn.__name__)
