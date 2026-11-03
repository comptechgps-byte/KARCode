#!/usr/bin/env python3
"""
paper_numbers_v5.py — macros for manuscript v5: Experiment 7 (14B with generic
tools), Experiment 8 (Llama-3.1-8B live), Experiment 9 (two more held-out
Boutique faults) and the offline rollback-trap study. Writes
paper/results_macros_v5.tex and paper/tables/prompt_traps.tex. Tolerates runs
that have not finished.

    python benchmark/paper_numbers_v5.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_v2 import fault_matrix, load  # noqa: E402
from paper_numbers import macro_name, pct  # noqa: E402
from paper_numbers_v4 import FAMILY, llm_events, nonempty  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
R, P = ROOT / "results", ROOT / "paper"
TRAPS = ["evicted", "transient-old-release", "oom-old-release"]
TRAP_SHORT = {"evicted": "evicted", "transient-old-release": "transient", "oom-old-release": "oom-growth"}
EXPS = {"ExpSeven": ("exp7a", "exp7b"), "ExpEight": ("exp8a", "exp8b"), "ExpNine": ("exp9",)}


def live(m: dict) -> None:
    for tag, dirs in EXPS.items():
        dirs = nonempty(*dirs)
        if not dirs:
            continue
        x = load(dirs, tag)
        m[macro_name("NTrials", tag)] = str(len(x))
        m[macro_name(tag, "UnsafeExecuted")] = str(int(x.unsafe_executed.fillna(0).sum()))
        fm = fault_matrix(x)
        for (tb, cond), g in fm.groupby(["testbed", "condition"]):
            m[macro_name(tag, tb, cond, "repaired")] = f"{int((g.verdict == 'repaired').sum())}/{len(g)}"
        for _, r in fm.iterrows():
            k = (tag, r.testbed, r.scenario, r.condition)
            m[macro_name(*k, "rec")] = f"{r.k}/{r.n}"
            m[macro_name(*k, "ttr")] = "--" if np.isnan(r.ttr_med) else f"{r.ttr_med:.0f}"
            if not np.isnan(r.user_down_med):
                m[macro_name(*k, "userdown")] = f"{r.user_down_med:.0f}"
        if tag == "ExpSeven":   # tau = 600 s; also score at the 300 s window of the other experiments
            x3 = x.copy()
            x3["recovered"] = x3.recovered & (x3.ttr_s <= 300)
            for _, r in fault_matrix(x3).iterrows():
                m[macro_name(tag, r.testbed, r.scenario, r.condition, "recthree")] = f"{r.k}/{r.n}"
        llm = llm_events(x, dirs)
        for cond, g in llm.groupby("cond"):
            lat = [d.get("latency_s") for d in g.llm if isinstance(d, dict) and d.get("latency_s")]
            if lat:
                m[macro_name(tag, cond, "llmlat")] = f"{np.median(lat):.0f}"
        # proposals per (fault, condition): most common LLM proposal and its share
        for (cond, fault), g in llm.assign(fault=llm.trial_id.str.extract(r"b\d+-(.+?)-(?:hybrid|llm)")[0]) \
                .groupby(["cond", "fault"]):
            acts = g.proposed_action.dropna().value_counts()
            if len(acts):
                m[macro_name(tag, cond, fault, "topaction")] = acts.index[0].replace("-", "\\mbox{-}")
                m[macro_name(tag, cond, fault, "topshare")] = pct(acts.iloc[0] / acts.sum())
        if tag == "ExpSeven":   # which kinds did the 14B model inspect with the generic tools?
            calls = [t for d in llm.llm if isinstance(d, dict) for t in d.get("tools", [])]
            seqs = [d.get("tools", []) for d in llm.llm if isinstance(d, dict)]
            m["ExpSevenToolCalls"] = str(len(calls))
            m["ExpSevenDiagnoses"] = str(len(llm))
            kinds = pd.Series(calls).str.split(":").str[1].dropna()
            for kind, share in kinds.value_counts(normalize=True).items():
                m[macro_name("ExpSevenKindShare", kind)] = pct(share)
            np_diag = llm[llm.trial_id.str.contains("net-partition")]
            np_calls = [t for d in np_diag.llm if isinstance(d, dict) for t in d.get("tools", [])]
            m["ExpSevenNetPartDiagnoses"] = str(len(np_diag))
            m["ExpSevenNetPartNetpolLookups"] = str(sum("networkpolicies" in t for t in np_calls))
            repeats = sum(sum(1 for i, t in enumerate(s) if t in s[:i]) for s in seqs)
            m["ExpSevenRepeatedCallShare"] = pct(repeats / max(1, len(calls)))
        if tag == "ExpNine":   # did the rule engine misfire on the probe fault?
            ev = []
            for d in dirs:
                from analyze_results import load_events
                e = load_events([d], x[x.run_dir == d])
                if len(e):
                    e = e.copy()
                    e["cond"] = e.trial_id.map(x.set_index("trial_id").condition)
                    ev.append(e)
            if ev:
                ev = pd.concat(ev, ignore_index=True)
                pr = ev[ev.trial_id.str.contains("probe-misconfig") & ev.cond.isin(["rules", "hybrid"])]
                m["ExpNineProbeRuleNetpol"] = str(int((pr.proposed_action == "revert-network-policy").sum()))
                m["ExpNineProbeRuleEvents"] = str(len(pr))


def traps(m: dict) -> None:
    frames = []
    for f in sorted((R / "prompt_traps").glob("*.csv")):
        if f.stem in FAMILY:
            d = pd.read_csv(f)
            d["model"] = f.stem
            frames.append(d)
    if not frames:
        return
    d = pd.concat(frames)
    d["correct"] = d.correct.astype(str).str.lower().eq("true")
    models = sorted(set(d.model), key=lambda s: FAMILY[s][1])
    lines = [r"\begin{tabular}{@{}l" + "c" * len(TRAPS) + "cc@{}}", r"\toprule",
             "Model & " + " & ".join(TRAP_SHORT[t] for t in TRAPS) + r" & Rollbacks & Correct \\", r"\midrule"]
    for mm in models:
        cells = []
        for t in TRAPS:
            g = d[(d.model == mm) & (d.fault == t) & (d.decoding == "greedy")]
            cells.append(f"{int(g.correct.sum())}/{len(g)}")
        g = d[d.model == mm]
        rb = (g.action == "rollback-deployment").mean()
        lines.append(f"{FAMILY[mm][0]} & " + " & ".join(cells) + f" & {pct(rb)} & {pct(g.correct.mean())} " + r"\\")
        m[macro_name("Trap", mm, "correct")] = pct(g.correct.mean())
        m[macro_name("Trap", mm, "rollback")] = pct(rb)
    lines += [r"\midrule", "Rollback-always & " + " & ".join(["0/3"] * len(TRAPS)) + r" & 100\% & 0\% \\",
              r"\bottomrule", r"\end{tabular}"]
    (P / "tables" / "prompt_traps.tex").write_text("\n".join(lines) + "\n")
    m["TrapModels"] = str(len(models))
    m["TrapCalls"] = str(len(d))
    m["TrapRollbackAll"] = pct((d.action == "rollback-deployment").mean())


def main():
    m: dict[str, str] = {}
    for step in (live, traps):
        try:
            step(m)
        except Exception as e:
            print(f"warning: {step.__name__}: {e}")
    all_dirs = nonempty("main", "exp2a", "exp2b", "exp3", "exp4a", "exp4b", "exp5a", "exp5b", "exp6a", "exp6b",
                        "exp2a_replication", "exp7a", "exp7b", "exp8a", "exp8b", "exp9")
    every = pd.concat([load([d], "all") for d in all_dirs], ignore_index=True)
    total = len(every)
    m["NFaultTypesTotalVfive"] = str(every.groupby("testbed").scenario.nunique().sum())
    m["NHeldOutVfive"] = str(every[every.category == "held-out"].scenario.nunique())
    m["NTrialsAllExperimentsVfive"] = f"{total:,}".replace(",", "{,}")
    for k in ("NTrialsExpSeven", "NTrialsExpEight", "NTrialsExpNine"):
        m.setdefault(k, "0")
    out = P / "results_macros_v5.tex"
    lines = ["% generated by benchmark/paper_numbers_v5.py — do not edit by hand"]
    lines += [f"\\newcommand{{\\{k}}}{{{v}}}" for k, v in sorted(m.items()) if re.fullmatch(r"[A-Za-z]+", k)]
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {len(lines) - 1} macros to {out}")


if __name__ == "__main__":
    main()
