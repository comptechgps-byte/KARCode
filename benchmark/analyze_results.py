#!/usr/bin/env python3
"""
analyze_results.py — statistics, tables and figures for the paper.

    python benchmark/analyze_results.py results/main --fig-dir paper/figures --tab-dir paper/tables

Time-to-recovery (TTR) is right-censored at the observation window, so the
primary analysis is survival-based:
  * recovery rate with Wilson 95% CI;
  * Kaplan-Meier median TTR (95% CI) and restricted mean survival time
    (RMST, mean time spent unrecovered within the window, bootstrap CI);
  * pairwise contrasts of every agent condition against `hybrid` within each
    scenario: Fisher's exact test (recovery, two-sided), log-rank test (TTR),
    RMST difference (bootstrap 95% CI); Holm correction across the family.
Decision quality comes from the agent's MTTR_EVENT log (agent_events.jsonl).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from lifelines import KaplanMeierFitter
from lifelines.statistics import logrank_test
from scipy.stats import fisher_exact
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.proportion import proportion_confint

SCEN_ORDER = ["oom-kill", "crashloop", "net-partition", "bad-image", "missing-config",
              "pod-kill", "transient-crash", "log-injection"]
COND_ORDER = ["baseline", "restart", "rules", "hybrid", "llm-only"]
COND_LABEL = {"baseline": "Baseline (native)", "restart": "Naive restart", "rules": "Rules",
              "hybrid": "Hybrid", "llm-only": "LLM-only"}
CORRECT = {"oom-kill": {"patch-limits"}, "crashloop": {"rollback-deployment"},
           "net-partition": {"revert-network-policy"}, "bad-image": {"rollback-deployment"},
           "missing-config": {"rollback-deployment"}, "pod-kill": {"noop", ""},
           "transient-crash": {"noop", ""}, "log-injection": {"rollback-deployment"}}
UNSAFE = {"cordon-node", "drain-node", "delete-pvc"}
# Validated categorical slots (dataviz reference palette, light); baseline = muted ink.
COLORS = {"baseline": "#898781", "restart": "#eda100", "rules": "#eb6834",
          "hybrid": "#2a78d6", "llm-only": "#1baf7a"}
STYLES = {"baseline": ":", "restart": "-.", "rules": "--", "hybrid": "-", "llm-only": (0, (5, 1, 1, 1))}
RNG = np.random.default_rng(7)


def km_curve(t: np.ndarray, e: np.ndarray):
    """Kaplan-Meier estimate: returns (event times, survival just after each)."""
    t, e = np.asarray(t, float), np.asarray(e, bool)
    times = np.unique(t[e])
    at_risk = np.array([(t >= u).sum() for u in times])
    deaths = np.array([((t == u) & e).sum() for u in times])
    return times, np.cumprod(1 - deaths / at_risk)


def rmst(t: np.ndarray, e: np.ndarray, tau: float) -> float:
    """Area under the KM survival curve on [0, tau]."""
    times, surv = km_curve(t, e)
    keep = times < tau
    knots = np.r_[0.0, times[keep], tau]
    levels = np.r_[1.0, surv[keep]]
    return float(np.sum(np.diff(knots) * levels))


def boot_rmst_diff(a, b, tau, n=2000):
    ta, ea = a
    tb, eb = b
    obs = rmst(ta, ea, tau) - rmst(tb, eb, tau)
    diffs = []
    for _ in range(n):
        ia, ib = RNG.integers(0, len(ta), len(ta)), RNG.integers(0, len(tb), len(tb))
        diffs.append(rmst(ta[ia], ea[ia], tau) - rmst(tb[ib], eb[ib], tau))
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return obs, lo, hi


def load_events(run_dirs, df: pd.DataFrame, window_s: float = 400) -> pd.DataFrame:
    """Agent decision records belonging to the *valid* run of each trial.
    A trial re-run (e.g. after a host suspension) reuses its trial_id, so
    records are also matched on time: only those whose diagnosis started
    within `window_s` after the valid row's t0 are kept."""
    rows = []
    for d in run_dirs:
        p = Path(d) / "agent_events.jsonl"
        if p.exists():
            rows += [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    ev = pd.DataFrame(rows)
    if not len(ev):
        return ev
    t0 = df.set_index("trial_id").t0_utc.map(lambda x: pd.Timestamp(x).timestamp())
    ev = ev[ev.trial_id.isin(t0.index)].copy()
    start = ev.trial_id.map(t0)
    ts = ev.t_diag_start.fillna(ev.t_detected)
    return ev[(ts >= start - 5) & (ts <= start + window_s)]


def sgn(x: float) -> str:
    """Signed one-decimal number without a negative zero."""
    s = f"{x:+.1f}"
    return "+0.0" if s == "-0.0" else s


def fmt_p(p: float) -> str:
    if np.isnan(p):
        return "--"
    return "$<$0.001" if p < 0.001 else f"{p:.3f}"


# ---------------------------------------------------------------------- #
def cell_summary(df: pd.DataFrame, tau: float) -> pd.DataFrame:
    rows = []
    for (s, c), g in df.groupby(["scenario", "condition"]):
        t, e = g.ttr_s.values.astype(float), g.recovered.values.astype(bool)
        k, n = int(e.sum()), len(g)
        lo, hi = proportion_confint(k, n, method="wilson")
        kmf = KaplanMeierFitter().fit(t, e)
        med = kmf.median_survival_time_
        rm = rmst(t, e, tau)
        boots = [rmst(t[i], e[i], tau) for i in (RNG.integers(0, n, n) for _ in range(1000))]
        rows.append({"scenario": s, "condition": c, "n": n, "recovered": k, "rate": k / n,
                     "rate_lo": lo, "rate_hi": hi,
                     "km_median_s": np.nan if np.isinf(med) else med,
                     "rmst_s": rm, "rmst_lo": np.percentile(boots, 2.5),
                     "rmst_hi": np.percentile(boots, 97.5),
                     "ttr_recovered_median_s": np.median(t[e]) if k else np.nan})
    out = pd.DataFrame(rows)
    out["scenario"] = pd.Categorical(out.scenario, SCEN_ORDER, ordered=True)
    out["condition"] = pd.Categorical(out.condition, COND_ORDER, ordered=True)
    return out.sort_values(["scenario", "condition"]).reset_index(drop=True)


def contrasts(df: pd.DataFrame, tau: float, ref: str = "hybrid") -> pd.DataFrame:
    rows = []
    for s in SCEN_ORDER:
        g = df[df.scenario == s]
        if ref not in set(g.condition):
            continue
        r = g[g.condition == ref]
        for c in COND_ORDER:
            if c == ref or c not in set(g.condition):
                continue
            o = g[g.condition == c]
            table = [[r.recovered.sum(), (~r.recovered).sum()], [o.recovered.sum(), (~o.recovered).sum()]]
            p_f = fisher_exact(table)[1]
            lr = logrank_test(r.ttr_s, o.ttr_s, r.recovered, o.recovered).p_value
            d, lo, hi = boot_rmst_diff((r.ttr_s.values, r.recovered.values),
                                       (o.ttr_s.values, o.recovered.values), tau)
            rows.append({"scenario": s, "vs": c, "rate_ref": r.recovered.mean(),
                         "rate_other": o.recovered.mean(), "p_fisher": p_f, "p_logrank": lr,
                         "rmst_diff_s": d, "rmst_diff_lo": lo, "rmst_diff_hi": hi})
    out = pd.DataFrame(rows)
    if len(out):
        for col in ("p_fisher", "p_logrank"):
            ok = out[col].notna()
            out.loc[ok, col + "_holm"] = multipletests(out.loc[ok, col], method="holm")[1]
    return out


def decisions(df: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Per (scenario, condition): first-action correctness, unsafe proposals,
    executions, collateral actions, LLM usage and latency."""
    rows = []
    agent = df[df.condition != "baseline"]
    for (s, c), g in agent.groupby(["scenario", "condition"]):
        ev = events[events.trial_id.isin(g.trial_id)] if len(events) else pd.DataFrame()
        first_ok = [a in CORRECT[s] or (pd.isna(a) and "" in CORRECT[s])
                    for a in g.first_action.fillna("")]
        lat = []
        if len(ev) and "llm" in ev:
            lat = [d.get("latency_s") for d in ev.llm.dropna() if isinstance(d, dict) and d.get("latency_s")]
        rows.append({
            "scenario": s, "condition": c, "n": len(g),
            "first_action_correct": float(np.mean(first_ok)),
            "trials_with_unsafe_proposal": int((g.unsafe_proposals > 0).sum()),
            "unsafe_executed": int(g.unsafe_executed.sum()),
            "scope_rejections": int(g.scope_rejections.sum()),
            "budget_rejections": int(g.budget_rejections.sum()),
            "actions_executed_mean": g.n_executed.mean(),
            "collateral_actions": int(g.collateral_actions.sum()),
            "stale_aborts": int(g.n_stale.sum()),
            "llm_calls_mean": g.n_llm_calls.mean(),
            "llm_latency_median_s": float(np.median(lat)) if lat else np.nan,
            "llm_latency_p95_s": float(np.percentile(lat, 95)) if lat else np.nan,
        })
    out = pd.DataFrame(rows)
    out["scenario"] = pd.Categorical(out.scenario, SCEN_ORDER, ordered=True)
    out["condition"] = pd.Categorical(out.condition, COND_ORDER, ordered=True)
    return out.sort_values(["scenario", "condition"]).reset_index(drop=True)


def latency_breakdown(df: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """For the first executed action of each successful agent trial the time
    to recovery splits exactly into
      detect   = t_detected - t0            (fault visible to the agent)
      wait     = t_diag_start - t_detected  (rule deferral, cool-down, earlier attempts)
      diagnose = t_diagnosed - t_diag_start (rule engine or LLM call)
      act      = t_acted - t_diagnosed      (verify, gate, API call)
      converge = TTR - (t_acted - t0)       (rollout until stably healthy)."""
    if not len(events):
        return pd.DataFrame()
    t0 = df.set_index("trial_id").t0_utc.map(lambda x: pd.Timestamp(x).timestamp())
    rows = []
    for tid, ev in events[events.executed == True].groupby("trial_id"):  # noqa: E712
        tr = df[df.trial_id == tid].iloc[0]
        if not tr.recovered or tid not in t0.index:
            continue
        e = ev.sort_values("t_acted").iloc[0]
        base = t0[tid]
        rows.append({"trial_id": tid, "scenario": tr.scenario, "condition": tr.condition,
                     "detect_s": e.t_detected - base,
                     "wait_s": e.t_diag_start - e.t_detected,
                     "diagnose_s": e.t_diagnosed - e.t_diag_start,
                     "act_s": e.t_acted - e.t_diagnosed,
                     "converge_s": tr.ttr_s - (e.t_acted - base)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------- #
def plot_km(df: pd.DataFrame, tau: float, path: Path):
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 8, "axes.edgecolor": "#c3c2b7", "axes.labelcolor": "#52514e",
                         "xtick.color": "#52514e", "ytick.color": "#52514e", "font.family": "DejaVu Sans"})
    scen = [s for s in SCEN_ORDER if s in set(df.scenario)]
    fig, axes = plt.subplots(2, 4, figsize=(7.2, 3.9), sharex=True, sharey=True)
    for ax, s in zip(axes.flat, scen):
        g = df[df.scenario == s]
        for j, c in enumerate(COND_ORDER):
            h = g[g.condition == c]
            if not len(h):
                continue
            kmf = KaplanMeierFitter().fit(h.ttr_s, h.recovered)
            sf = 1 - kmf.survival_function_.iloc[:, 0]      # cumulative recovery
            # small vertical offsets keep curves that coincide at 0 or 1 distinguishable
            off = (j - (len(COND_ORDER) - 1) / 2) * 0.012
            ax.step(sf.index, sf.values + off, where="post", color=COLORS[c], ls=STYLES[c], lw=1.6,
                    label=COND_LABEL[c])
        ax.set_title(s, fontsize=8, color="#0b0b0b")
        ax.set_xlim(0, tau)
        ax.set_ylim(-0.02, 1.04)
        ax.grid(True, color="#e1e0d9", lw=0.5)
        ax.spines[["top", "right"]].set_visible(False)
    for ax in axes[1]:
        ax.set_xlabel("time since fault injection (s)")
    for ax in axes[:, 0]:
        ax.set_ylabel("fraction recovered")
    from matplotlib.lines import Line2D
    present = [c for c in COND_ORDER if c in set(df.condition)]
    handles = [Line2D([], [], color=COLORS[c], ls=STYLES[c], lw=1.6) for c in present]
    fig.legend(handles, [COND_LABEL[c] for c in present], loc="upper center", ncol=5,
               frameon=False, fontsize=7.5)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(path, dpi=300)
    plt.close(fig)


def plot_recovery(summary: pd.DataFrame, path: Path):
    import matplotlib.pyplot as plt
    scen = [s for s in SCEN_ORDER if s in set(summary.scenario.astype(str))]
    conds = [c for c in COND_ORDER if c in set(summary.condition.astype(str))]
    fig, ax = plt.subplots(figsize=(7.2, 2.6))
    w = 0.8 / len(conds)
    for i, c in enumerate(conds):
        sub = summary[summary.condition == c].set_index("scenario").reindex(scen)
        x = np.arange(len(scen)) + (i - (len(conds) - 1) / 2) * w
        ax.bar(x, sub.rate, width=w * 0.88, color=COLORS[c], label=COND_LABEL[c], zorder=2)
        ax.errorbar(x, sub.rate, yerr=[sub.rate - sub.rate_lo, sub.rate_hi - sub.rate],
                    fmt="none", ecolor="#52514e", elinewidth=0.7, capsize=1.5, zorder=3)
    ax.set_xticks(np.arange(len(scen)), scen, fontsize=7.5)
    ax.set_ylabel("recovery rate (95% CI)")
    ax.set_ylim(0, 1.08)
    ax.grid(True, axis="y", color="#e1e0d9", lw=0.5, zorder=0)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(ncol=5, frameon=False, fontsize=7, loc="upper center", bbox_to_anchor=(0.5, 1.2))
    fig.tight_layout()
    fig.savefig(path, dpi=300)
    plt.close(fig)


def plot_breakdown(bd: pd.DataFrame, path: Path):
    import matplotlib.pyplot as plt
    if not len(bd):
        return
    parts = [("detect_s", "detect", "#b7d3f6"), ("wait_s", "wait (defer/cool-down)", "#e1e0d9"),
             ("diagnose_s", "diagnose", "#2a78d6"), ("act_s", "act", "#104281"),
             ("converge_s", "converge", "#898781")]
    grp = bd.groupby(["scenario", "condition"])[[p[0] for p in parts]].median().reset_index()
    grp = grp[grp.condition.isin(["rules", "hybrid", "llm-only"])]
    grp["label"] = grp.scenario + " / " + grp.condition
    grp["scenario"] = pd.Categorical(grp.scenario, SCEN_ORDER, ordered=True)
    grp["condition"] = pd.Categorical(grp.condition, COND_ORDER, ordered=True)
    grp = grp.sort_values(["scenario", "condition"])
    fig, ax = plt.subplots(figsize=(7.2, 0.22 * len(grp) + 0.9))
    left = np.zeros(len(grp))
    for col, name, color in parts:
        vals = grp[col].clip(lower=0).values
        ax.barh(np.arange(len(grp)), vals, left=left, color=color, label=name,
                edgecolor="#fcfcfb", linewidth=1.0, zorder=2)
        left += vals
    ax.set_yticks(np.arange(len(grp)), grp.label, fontsize=7)
    ax.invert_yaxis()
    ax.set_xlabel("median seconds (successful trials)")
    ax.grid(True, axis="x", color="#e1e0d9", lw=0.5, zorder=0)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(ncol=4, frameon=False, fontsize=7, loc="lower center", bbox_to_anchor=(0.5, 1.0))
    fig.tight_layout()
    fig.savefig(path, dpi=300)
    plt.close(fig)


# ---------------------------------------------------------------------- #
def tex_summary(summary: pd.DataFrame, path: Path):
    lines = [r"\begin{tabular}{@{}llrrr@{}}", r"\toprule",
             r"Scenario & Condition & Recovered & KM median & RMST (s) \\",
             r" & & [rate 95\% CI, \%] & (s) & [95\% CI] \\",
             r"\midrule"]
    prev = None
    for _, r in summary.iterrows():
        s = str(r.scenario)
        if prev is not None and s != prev:
            lines.append(r"\addlinespace")
        med = "--" if np.isnan(r.km_median_s) else f"{r.km_median_s:.1f}"
        lines.append(f"{s if s != prev else ''} & {COND_LABEL[str(r.condition)]} & "
                     f"{r.recovered}/{r.n} [{100*r.rate_lo:.0f}, {100*r.rate_hi:.0f}] & "
                     f"{med} & {r.rmst_s:.1f} [{r.rmst_lo:.1f}, {r.rmst_hi:.1f}] \\\\")
        prev = s
    lines += [r"\bottomrule", r"\end{tabular}"]
    path.write_text("\n".join(lines) + "\n")


def tex_contrasts(ct: pd.DataFrame, path: Path):
    lines = [r"\begin{tabular}{@{}llrrrr@{}}", r"\toprule",
             r"Scenario & Hybrid vs. & $\Delta$ rate (pp) & $p_{\mathrm{Fisher}}^{\mathrm{Holm}}$ & "
             r"$p_{\mathrm{log\text{-}rank}}^{\mathrm{Holm}}$ & $\Delta$RMST (s) [95\% CI] \\", r"\midrule"]
    prev = None
    for _, r in ct.iterrows():
        if prev is not None and r.scenario != prev:
            lines.append(r"\addlinespace")
        lines.append(f"{r.scenario if r.scenario != prev else ''} & {COND_LABEL[r.vs]} & "
                     f"{100*(r.rate_ref-r.rate_other):+.0f} & {fmt_p(r.p_fisher_holm)} & "
                     f"{fmt_p(r.p_logrank_holm)} & {sgn(r.rmst_diff_s)} [{sgn(r.rmst_diff_lo)}, {sgn(r.rmst_diff_hi)}] \\\\")
        prev = r.scenario
    lines += [r"\bottomrule", r"\end{tabular}"]
    path.write_text("\n".join(lines) + "\n")


def tex_decisions(dec: pd.DataFrame, path: Path):
    lines = [r"\begin{tabular}{@{}llrrrrrr@{}}", r"\toprule",
             r"Scenario & Condition & 1st action & Actions & Unsafe & Unsafe & Collateral & LLM latency \\",
             r" & & correct & /trial & proposed & executed & actions & median (s) \\", r"\midrule"]
    prev = None
    for _, r in dec.iterrows():
        s = str(r.scenario)
        if prev is not None and s != prev:
            lines.append(r"\addlinespace")
        lat = "--" if np.isnan(r.llm_latency_median_s) else f"{r.llm_latency_median_s:.1f}"
        lines.append(f"{s if s != prev else ''} & {COND_LABEL[str(r.condition)]} & "
                     f"{100*r.first_action_correct:.0f}\\% & {r.actions_executed_mean:.1f} & "
                     f"{r.trials_with_unsafe_proposal}/{r.n} & {r.unsafe_executed} & "
                     f"{r.collateral_actions} & {lat} \\\\")
        prev = s
    lines += [r"\bottomrule", r"\end{tabular}"]
    path.write_text("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dirs", nargs="+")
    ap.add_argument("--fig-dir", default=None)
    ap.add_argument("--tab-dir", default=None)
    ap.add_argument("--tau", type=float, default=180)
    args = ap.parse_args()

    df = pd.concat([pd.read_csv(Path(d) / "trials.csv") for d in args.run_dirs], ignore_index=True)
    n_invalid = int((df.valid != True).sum())  # noqa: E712
    df = df[df.valid == True].copy()  # noqa: E712
    df = df.drop_duplicates("trial_id", keep="last")
    df["recovered"] = df.recovered.astype(str).str.lower().eq("true")
    for col in ("n_executed", "n_stale", "n_llm_calls", "unsafe_proposals", "unsafe_executed",
                "scope_rejections", "budget_rejections", "collateral_actions"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    events = load_events(args.run_dirs, df)

    summary = cell_summary(df, args.tau)
    ct = contrasts(df, args.tau)
    dec = decisions(df, events)
    bd = latency_breakdown(df, events)

    out = Path(args.run_dirs[0])
    summary.to_csv(out / "summary.csv", index=False)
    ct.to_csv(out / "contrasts.csv", index=False)
    dec.to_csv(out / "decisions.csv", index=False)
    bd.to_csv(out / "latency_breakdown.csv", index=False)

    pd.set_option("display.width", 200, "display.max_columns", 30, "display.max_rows", 200)
    print(f"valid trials: {len(df)}  (excluded invalid: {n_invalid})\n")
    print(summary.round(2).to_string(index=False), "\n")
    print(ct.round(4).to_string(index=False), "\n")
    print(dec.round(2).to_string(index=False), "\n")
    if len(bd):
        print(bd.groupby(["scenario", "condition"])[["detect_s", "wait_s", "diagnose_s", "act_s", "converge_s"]]
              .median().round(1).to_string())
    if len(events) and "llm" in events:
        lat = [d["latency_s"] for d in events.llm.dropna() if isinstance(d, dict) and d.get("latency_s")]
        err = sum(1 for d in events.llm.dropna() if isinstance(d, dict) and d.get("error"))
        if lat:
            print(f"\nLLM calls={len(lat)} errors={err} latency median={np.median(lat):.1f}s "
                  f"p95={np.percentile(lat, 95):.1f}s max={max(lat):.1f}s")

    if args.fig_dir:
        fd = Path(args.fig_dir)
        fd.mkdir(parents=True, exist_ok=True)
        plot_km(df, args.tau, fd / "km_recovery.png")
        plot_recovery(summary, fd / "recovery_rate.png")
        plot_breakdown(bd, fd / "latency_breakdown.png")
    if args.tab_dir:
        td = Path(args.tab_dir)
        td.mkdir(parents=True, exist_ok=True)
        tex_summary(summary, td / "summary.tex")
        tex_contrasts(ct, td / "contrasts.tex")
        tex_decisions(dec, td / "decisions.tex")


if __name__ == "__main__":
    main()
