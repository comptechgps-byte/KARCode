#!/usr/bin/env python3
"""
paper_numbers_v6.py — macros for manuscript v6: Experiment 10 (failure-triggered
escalation, agent v3.1), the extended rollback-trap study (seven traps), the
human-approval share over all experiments, and grand totals. Writes
paper/results_macros_v6.tex and paper/tables/prompt_traps.tex (replaces the v5
three-trap table).

    python benchmark/paper_numbers_v6.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_results import load_events  # noqa: E402
from analyze_v2 import decisive_tiers, fault_matrix, load  # noqa: E402
from paper_numbers import macro_name, pct  # noqa: E402
from paper_numbers_v4 import FAMILY, nonempty  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
R, P = ROOT / "results", ROOT / "paper"
ALL_RUNS = ["main", "exp2a", "exp2b", "exp3", "exp4a", "exp4b", "exp5a", "exp5b", "exp6a", "exp6b",
            "exp2a_replication", "exp7a", "exp7b", "exp8a", "exp8b", "exp9", "exp10a", "exp10b", "exp10c"]
TRAPS = ["transient-old-release", "oom-old-release", "dependency-down", "external-config-change",
         "node-lost", "cert-expired", "evicted"]
TRAP_SHORT = {"transient-old-release": "transient", "oom-old-release": "oom-growth",
              "dependency-down": "dep.-down", "external-config-change": "ext.-config",
              "node-lost": "node-lost", "cert-expired": "cert", "evicted": "evicted$^a$"}


def exp10(m: dict) -> None:
    dirs = nonempty("exp10a", "exp10b", "exp10c")
    if not dirs:
        return
    x = load(dirs, "ExpTen")
    m["NTrialsExpTen"] = str(len(x))
    m["ExpTenUnsafeExecuted"] = str(int(x.unsafe_executed.fillna(0).sum()))
    for _, r in fault_matrix(x).iterrows():
        k = ("ExpTen", r.testbed, r.scenario, r.condition)
        m[macro_name(*k, "rec")] = f"{r.k}/{r.n}"
        m[macro_name(*k, "ttr")] = "--" if np.isnan(r.ttr_med) else f"{r.ttr_med:.0f}"
    # escalations caused by a failed rule action, and LLM calls on the regression faults
    for d in dirs:
        xd = x[x.run_dir == d]
        ev = load_events([d], xd)
        if not len(ev):
            continue
        ev = ev.copy()
        ev["cond"] = ev.trial_id.map(xd.set_index("trial_id").condition)
        ev["scen"] = ev.trial_id.map(xd.set_index("trial_id").scenario)
        llm = ev[ev.diagnosis_source == "llm"]
        tag = Path(d).name
        if tag == "exp10a":
            esc = llm[llm.rule_hint.fillna("").str.contains("already tried and failed")]
            m["ExpTenEscalations"] = str(len(esc))
            m["ExpTenEscalatedTrials"] = str(esc.trial_id.nunique())
            for cond, g in llm.groupby("cond"):
                acts = g.proposed_action.dropna().value_counts()
                if len(acts):
                    m[macro_name("ExpTen", cond, "topaction")] = acts.index[0].replace("-", "\\mbox{-}")
                    m[macro_name("ExpTen", cond, "topshare")] = pct(acts.iloc[0] / acts.sum())
                lat = [v.get("latency_s") for v in g.llm if isinstance(v, dict) and v.get("latency_s")]
                if lat:
                    m[macro_name("ExpTen", cond, "llmlat")] = f"{np.median(lat):.0f}"
            first = ev[ev.diagnosis_source == "rules"].groupby("trial_id").t_acted.min()
            esc_t = esc.groupby("trial_id").t_diag_start.min()
            delay = (esc_t - first).dropna()
            if len(delay):
                m["ExpTenEscalationDelay"] = f"{delay.median():.0f}"
    # regression (exp10b, exp10c): trials, LLM calls and escalations on rule-covered /
    # self-healing faults, counted from the trial records and raw event logs
    reg = x[x.run_dir.isin([d for d in dirs if Path(d).name in ("exp10b", "exp10c")])]
    if len(reg):
        m["ExpTenRegressionTrials"] = str(len(reg))
        m["ExpTenRegressionRepaired"] = str(int(reg.recovered.sum()))
        m["ExpTenRegressionLlmCalls"] = str(int(pd.to_numeric(reg.n_llm_calls, errors="coerce").fillna(0).sum()))
        esc = 0
        for d in dirs:
            if Path(d).name in ("exp10b", "exp10c"):
                esc += sum("already tried and failed" in l for l in open(Path(d) / "agent_events.jsonl"))
        m["ExpTenRegressionEscalations"] = str(esc)


def traps(m: dict) -> None:
    frames = []
    for sub in ("prompt_traps", "prompt_traps2"):
        for f in sorted((R / sub).glob("*.csv")):
            if f.stem in FAMILY:
                d = pd.read_csv(f)
                d["model"] = f.stem
                frames.append(d)
    if not frames:
        return
    d = pd.concat(frames)
    d["correct"] = d.correct.astype(str).str.lower().eq("true")
    d = d[d.fault.isin(TRAPS)]
    traps_present = [t for t in TRAPS if t in set(d.fault)]
    scored = [t for t in traps_present if t != "evicted"]
    models = sorted(set(d.model), key=lambda s: FAMILY[s][1])
    short = {"qwen2.5_3b-instruct": "Q 3B", "llama3.2_3b": "L 3B", "qwen2.5_7b-instruct": "Q 7B",
             "llama3.1_8b": "L 8B", "gemma2_9b": "G 9B", "qwen2.5_14b-instruct": "Q 14B"}
    # transposed (traps x models) so that the table fits the text width
    lines = [r"\begin{tabular}{@{}l" + "c" * len(models) + "c@{}}", r"\toprule",
             "Trap & " + " & ".join(short.get(mm, mm) for mm in models) + r" & Always rollback \\", r"\midrule"]
    for t in traps_present:
        cells = []
        for mm in models:
            g = d[(d.model == mm) & (d.fault == t) & (d.decoding == "greedy")]
            cells.append(f"{int(g.correct.sum())}/{len(g)}")
        lines.append(f"{TRAP_SHORT[t]} & " + " & ".join(cells) + r" & 0/3 \\")
    lines.append(r"\midrule")
    rb_cells, ok_cells = [], []
    for mm in models:
        g = d[(d.model == mm) & d.fault.isin(scored)]
        rb, ok = (g.action == "rollback-deployment").mean(), g.correct.mean()
        rb_cells.append(pct(rb))
        ok_cells.append(pct(ok))
        m[macro_name("TrapAll", mm, "correct")] = pct(ok)
        m[macro_name("TrapAll", mm, "rollback")] = pct(rb)
        for t in scored:
            gt = d[(d.model == mm) & (d.fault == t)]
            m[macro_name("Trap", mm, t, "rollbacks")] = f"{int((gt.action == 'rollback-deployment').sum())}/{len(gt)}"
    lines += ["Rollbacks$^b$ & " + " & ".join(rb_cells) + r" & 100\% \\",
              "Correct$^b$ & " + " & ".join(ok_cells) + r" & 0\% \\", r"\bottomrule", r"\end{tabular}"]
    (P / "tables" / "prompt_traps.tex").write_text("\n".join(lines) + "\n")
    m["TrapAllCalls"] = str(len(d))
    m["TrapAllScoredCalls"] = str(len(d[d.fault.isin(scored)]))
    m["TrapAllN"] = str(len(traps_present))
    m["TrapAllScoredN"] = str(len(scored))
    # rank correlation: does doing well live (long tail) go with rolling back on traps?
    rb = d[d.fault.isin(scored)].assign(rb=lambda f: f.action == "rollback-deployment").groupby("model").rb.mean()
    m["TrapAllRollbackMin"] = pct(rb.min())
    m["TrapAllRollbackMax"] = pct(rb.max())


def approval(m: dict) -> None:
    parts = []
    for name in ALL_RUNS:
        dirs = nonempty(name)
        if not dirs:
            continue
        x = load(dirs, name)
        t = decisive_tiers(x, dirs)
        if len(t):
            parts.append(t)
    t = pd.concat(parts, ignore_index=True)
    acted = t[t.tier != "none (self-healed)"]
    m["DecisiveActedAll"] = str(len(acted))
    m["DecisiveModerateAll"] = str(int((acted.tier == "moderate").sum()))
    m["DecisiveModerateShareAll"] = pct((acted.tier == "moderate").mean())


def main():
    m: dict[str, str] = {}
    for step in (exp10, traps, approval):
        try:
            step(m)
        except Exception as e:
            print(f"warning: {step.__name__}: {e}")
    every = pd.concat([load([d], "all") for d in nonempty(*ALL_RUNS)], ignore_index=True)
    m["NTrialsAllExperimentsVsix"] = f"{len(every):,}".replace(",", "{,}")
    m["NUnsafeExecutedAll"] = str(int(every.unsafe_executed.fillna(0).sum()))
    m.setdefault("NTrialsExpTen", "0")
    out = P / "results_macros_v6.tex"
    lines = ["% generated by benchmark/paper_numbers_v6.py — do not edit by hand"]
    lines += [f"\\newcommand{{\\{k}}}{{{v}}}" for k, v in sorted(m.items()) if re.fullmatch(r"[A-Za-z]+", k)]
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {len(lines) - 1} macros to {out}")


if __name__ == "__main__":
    main()
