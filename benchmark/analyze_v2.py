#!/usr/bin/env python3
"""
analyze_v2.py — analysis for manuscript v2 (Experiments 2 and 3), plus the
fault-type-level summary across all experiments.

    python benchmark/analyze_v2.py \
        --exp1 results/main --exp2 results/exp2a results/exp2b --exp3 results/exp3 \
        --fig-dir paper/figures --tab-dir paper/tables --macros paper/results_macros_v2.tex

Unit of analysis: LLM decoding is greedy and seeded, so
repeated trials of one fault type under one policy are close to deterministic
and mainly measure timing noise. Outcomes are therefore reported per fault
type (repaired / partially / not repaired) with trial counts, and the pooled
statistics of v1 are kept only as secondary, per-cell descriptions.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from analyze_results import KaplanMeierFitter, load_events, rmst  # noqa: F401  (validated helpers)

COND_LABEL = {
    "baseline": "Native", "restart": "Naive restart", "rules": "Rules",
    "hybrid": "Hybrid", "llm-only": "LLM-only",
    "hybrid-tools": "Hybrid+tools", "llm-tools": "LLM+tools",
    "hybrid-14b": "Hybrid (14B)", "llm-only-14b": "LLM-only (14B)",
    "llm-tools-14b": "LLM+tools (14B)", "hybrid-tools-14b": "Hybrid+tools (14B)",
    "hybrid-generic": "Hybrid+generic", "llm-generic": "LLM+generic",
}
COND_ORDER = list(COND_LABEL)
SCEN_ORDER = {
    "demo": ["oom-kill", "crashloop", "net-partition", "bad-image", "missing-config",
             "pod-kill", "transient-crash", "log-injection"],
    "boutique": ["bad-image", "crash-release", "memory-release", "missing-secret",
                 "nonroot-release", "probe-misconfig", "unschedulable", "zero-replicas", "pod-kill"],
}
# Validated slots (dataviz reference palette, light); native = muted ink
COLORS = {"baseline": "#898781", "restart": "#eda100", "rules": "#eb6834", "hybrid": "#2a78d6",
          "llm-only": "#1baf7a", "hybrid-tools": "#e87ba4", "llm-tools": "#4a3aa7"}
STYLES = {"baseline": ":", "restart": "-.", "rules": "--", "hybrid": "-", "llm-only": (0, (5, 1, 1, 1)),
          "hybrid-tools": (0, (1, 1)), "llm-tools": (0, (6, 2))}


def load(run_dirs: list[str], experiment: str) -> pd.DataFrame:
    frames = []
    for d in run_dirs:
        p = Path(d) / "trials.csv"
        if p.exists():
            f = pd.read_csv(p)
            f["run_dir"] = d
            frames.append(f)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df = df[df.valid.astype(str) == "True"].drop_duplicates(["run_dir", "trial_id"], keep="last").copy()
    if "testbed" not in df:
        df["testbed"] = "demo"
    df["testbed"] = df.testbed.fillna("demo")
    df["experiment"] = experiment
    df["recovered"] = df.recovered.astype(str).str.lower().eq("true")
    for col in ("n_executed", "n_llm_calls", "unsafe_proposals", "unsafe_executed", "collateral_actions",
                "budget_rejections", "llm_latency_s_total", "user_down_s"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def verdict(k: int, n: int) -> str:
    return "repaired" if k == n else ("not repaired" if k == 0 else "partial")


def fault_matrix(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (exp, tb, s, c), g in df.groupby(["experiment", "testbed", "scenario", "condition"]):
        k, n = int(g.recovered.sum()), len(g)
        ttr = g.ttr_s[g.recovered]
        rows.append({"experiment": exp, "testbed": tb, "scenario": s, "condition": c, "k": k, "n": n,
                     "verdict": verdict(k, n), "ttr_med": ttr.median() if k else np.nan,
                     "rmst": rmst(g.ttr_s.values, g.recovered.values, 180),
                     "user_down_med": g.user_down_s.median() if "user_down_s" in g and g.user_down_s.notna().any() else np.nan})
    return pd.DataFrame(rows)


def tex_matrix(m: pd.DataFrame, testbed: str, conds: list[str], path: Path, caption_note: str = ""):
    sub = m[m.testbed == testbed]
    conds = [c for c in conds if c in set(sub.condition)]
    scen = [s for s in SCEN_ORDER[testbed] if s in set(sub.scenario)]
    def stacked(label: str) -> str:   # two-line headers keep wide tables within the text block
        parts = label.replace("+", "\n+").replace(" (", "\n(").replace("Naive restart", "Naive\nrestart").split("\n")
        return r"\shortstack{" + r"\\".join(parts) + "}" if len(parts) > 1 else label
    head = " & ".join(stacked(COND_LABEL[c]) for c in conds)
    lines = [r"\begin{tabular}{@{}l" + "c" * len(conds) + "@{}}", r"\toprule",
             f"Fault & {head} \\\\", r"\midrule"]
    for s in scen:
        cells = []
        for c in conds:
            r = sub[(sub.scenario == s) & (sub.condition == c)]
            if r.empty:
                cells.append("")
                continue
            r = r.iloc[0]
            mark = {"repaired": r"\ding{51}", "partial": r"$\circ$", "not repaired": r"\ding{55}"}[r.verdict]
            t = "" if np.isnan(r.ttr_med) else f" {r.ttr_med:.0f}\\,s"
            cells.append(f"{mark} {r.k}/{r.n}{t}")
        lines.append(f"\\code{{{s}}} & " + " & ".join(cells) + r" \\")
    # fault types repaired per condition
    tot = []
    for c in conds:
        rr = sub[sub.condition == c]
        tot.append(f"{int((rr.verdict == 'repaired').sum())}/{len(rr)}")
    lines += [r"\midrule", "Fault types repaired & " + " & ".join(tot) + r" \\", r"\bottomrule", r"\end{tabular}"]
    path.write_text("\n".join(lines) + "\n")


def plot_km(df: pd.DataFrame, testbed: str, conds: list[str], path: Path, tau: float = 180):
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    plt.rcParams.update({"font.size": 8, "axes.edgecolor": "#c3c2b7", "axes.labelcolor": "#52514e",
                         "xtick.color": "#52514e", "ytick.color": "#52514e"})
    sub = df[df.testbed == testbed]
    scen = [s for s in SCEN_ORDER[testbed] if s in set(sub.scenario)]
    conds = [c for c in conds if c in set(sub.condition)]
    ncols = 4
    nrows = int(np.ceil(len(scen) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(7.2, 2.0 * nrows + 0.5), sharex=True, sharey=True)
    axes = np.atleast_1d(axes).ravel()
    for ax, s in zip(axes, scen):
        g = sub[sub.scenario == s]
        for j, c in enumerate(conds):
            h = g[g.condition == c]
            if not len(h):
                continue
            kmf = KaplanMeierFitter().fit(h.ttr_s, h.recovered)
            sf = 1 - kmf.survival_function_.iloc[:, 0]
            # small vertical offsets keep curves that stay at 0 or 1 distinguishable
            off = (j - (len(conds) - 1) / 2) * 0.012
            ax.step(sf.index, sf.values + off, where="post", color=COLORS.get(c, "#52514e"),
                    ls=STYLES.get(c, "-"), lw=1.5)
        ax.set_title(s, fontsize=8)
        ax.set_xlim(0, tau)
        ax.set_ylim(-0.06, 1.06)
        ax.grid(True, color="#e1e0d9", lw=0.5)
        ax.spines[["top", "right"]].set_visible(False)
    for ax in axes[len(scen):]:
        ax.set_visible(False)
    for ax in axes[-ncols:]:
        ax.set_xlabel("seconds since injection")
    handles = [Line2D([], [], color=COLORS.get(c, "#52514e"), ls=STYLES.get(c, "-"), lw=1.5) for c in conds]
    fig.legend(handles, [COND_LABEL[c] for c in conds], loc="upper center", ncol=len(conds),
               frameon=False, fontsize=7)
    fig.supylabel("fraction recovered", fontsize=8)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(path, dpi=300)
    plt.close(fig)


def decisions(df: pd.DataFrame, run_dirs: list[str]) -> pd.DataFrame:
    ev = load_events(run_dirs, df) if len(df) else pd.DataFrame()
    rows = []
    for (s, c), g in df[df.condition != "baseline"].groupby(["scenario", "condition"]):
        e = ev[ev.trial_id.isin(g.trial_id)] if len(ev) else pd.DataFrame()
        llm = e[e.diagnosis_source.isin(["llm"])] if len(e) else e
        lat = [d.get("latency_s") for d in llm.get("llm", pd.Series(dtype=object)).dropna()
               if isinstance(d, dict) and d.get("latency_s")]
        tools = [t for d in llm.get("llm", pd.Series(dtype=object)).dropna()
                 if isinstance(d, dict) for t in d.get("tools", [])]
        rows.append({"scenario": s, "condition": c, "n": len(g),
                     "first_actions": "; ".join(f"{a or 'none'}:{k}" for a, k in
                                                g.first_action.fillna("").value_counts().items()),
                     "llm_calls": int(g.n_llm_calls.sum()),
                     "llm_lat_med": float(np.median(lat)) if lat else np.nan,
                     "tool_calls": len(tools),
                     "tools_used": "; ".join(f"{t}:{k}" for t, k in pd.Series(tools).value_counts().items()),
                     "unsafe_executed": int(g.unsafe_executed.fillna(0).sum()),
                     "collateral": int(g.collateral_actions.fillna(0).sum()),
                     "budget_rej": int(g.budget_rejections.fillna(0).sum())})
    return pd.DataFrame(rows)


def decisive_tiers(df: pd.DataFrame, run_dirs: list[str]) -> pd.DataFrame:
    """For every recovered agent trial: the risk tier of the decisive action,
    i.e. the last action executed on the target before recovery began. Moderate
    actions would wait for a human approver under the production policy, so
    this quantifies how much of the unattended speed depends on auto-approval."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))
    from safety import ACTION_RISK
    ev = load_events(run_dirs, df)
    if not len(ev):
        return pd.DataFrame()
    t0 = df.set_index("trial_id").t0_utc.map(lambda x: pd.Timestamp(x).timestamp())
    rows = []
    for _, tr in df[(df.condition != "baseline") & df.recovered].iterrows():
        e = ev[(ev.trial_id == tr.trial_id) & (ev.deployment == tr.target) & (ev.executed == True)]  # noqa: E712
        e = e[e.t_acted <= t0[tr.trial_id] + tr.ttr_s]
        if e.empty:
            rows.append({"scenario": tr.scenario, "condition": tr.condition, "tier": "none (self-healed)",
                         "action": ""})
            continue
        last = e.sort_values("t_acted").iloc[-1]
        rows.append({"scenario": tr.scenario, "condition": tr.condition,
                     "tier": ACTION_RISK.get(last.proposed_action, "destructive"),
                     "action": last.proposed_action})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp1", nargs="*", default=[])
    ap.add_argument("--exp2", nargs="*", default=[])
    ap.add_argument("--exp3", nargs="*", default=[])
    ap.add_argument("--fig-dir")
    ap.add_argument("--tab-dir")
    ap.add_argument("--out", default="results/v2_analysis")
    args = ap.parse_args()

    e1, e2, e3 = load(args.exp1, "exp1"), load(args.exp2, "exp2"), load(args.exp3, "exp3")
    allx = pd.concat([x for x in (e1, e2, e3) if len(x)], ignore_index=True)
    m = fault_matrix(allx)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    m.to_csv(out / "fault_matrix.csv", index=False)
    pd.set_option("display.width", 220, "display.max_rows", 400, "display.max_colwidth", 80)
    for exp in ("exp1", "exp2", "exp3"):
        sub = m[m.experiment == exp]
        if len(sub):
            print(f"== {exp}")
            print(sub.pivot_table(index=["testbed", "scenario"], columns="condition",
                                  values="k", aggfunc="first").to_string(), "\n")
    for exp, x, dirs in (("exp2", e2, args.exp2), ("exp3", e3, args.exp3)):
        if len(x):
            d = decisions(x, dirs)
            d.to_csv(out / f"decisions_{exp}.csv", index=False)
            print(f"== decisions {exp}\n", d.to_string(index=False), "\n")
    tiers = []
    for x, dirs in ((e1, args.exp1), (e2, args.exp2), (e3, args.exp3)):
        if len(x):
            t = decisive_tiers(x, dirs)
            if len(t):
                t["experiment"] = x.experiment.iloc[0]
                tiers.append(t)
    if tiers:
        tiers = pd.concat(tiers, ignore_index=True)
        tiers.to_csv(out / "decisive_tiers.csv", index=False)
        print("== decisive action tier of recovered agent trials\n",
              tiers.groupby(["experiment", "tier"]).size().to_string(), "\n")
    if len(e3) and "user_down_s" in e3:
        print("== exp3 user-facing downtime (median s)\n",
              e3.pivot_table(index="scenario", columns="condition", values="user_down_s",
                             aggfunc="median").round(1).to_string())

    if args.tab_dir:
        td = Path(args.tab_dir)
        td.mkdir(parents=True, exist_ok=True)
        if len(e1):
            tex_matrix(m[m.experiment == "exp1"], "demo",
                       ["baseline", "restart", "rules", "hybrid", "llm-only"], td / "matrix_exp1.tex")
        if len(e2):
            tex_matrix(m[m.experiment == "exp2"], "demo",
                       ["hybrid", "llm-only", "hybrid-tools", "llm-tools",
                        "hybrid-14b", "llm-only-14b", "llm-tools-14b"], td / "matrix_exp2.tex")
        if len(e3):
            tex_matrix(m[m.experiment == "exp3"], "boutique",
                       ["baseline", "restart", "rules", "hybrid", "hybrid-tools", "llm-tools"],
                       td / "matrix_exp3.tex")
    if args.fig_dir and len(e3):
        fd = Path(args.fig_dir)
        fd.mkdir(parents=True, exist_ok=True)
        plot_km(e3, "boutique", ["baseline", "restart", "rules", "hybrid", "hybrid-tools", "llm-tools"],
                fd / "km_boutique.png", tau=300)   # Exp 3 observation window


if __name__ == "__main__":
    main()
