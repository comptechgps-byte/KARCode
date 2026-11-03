#!/usr/bin/env python3
"""
tables_v4.py — consolidated fault-type tables for manuscript v4: one table per
testbed, rows = (experiment, policy), columns = fault types, cells = outcome
symbol and recovered/total trials. Median times-to-recovery go to an appendix
table per testbed.

    python benchmark/tables_v4.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_v2 import SCEN_ORDER, fault_matrix, load  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
R, T = ROOT / "results", ROOT / "paper" / "tables"

RUNS = [  # (experiment label, run dirs)
    ("1", ["main"]), ("2", ["exp2a", "exp2b"]), ("2R", ["exp2a_replication"]), ("3", ["exp3"]),
    ("4", ["exp4a", "exp4b"]), ("5", ["exp5a", "exp5b"]), ("6", ["exp6a", "exp6b"]), ("7", ["exp7a", "exp7b"]),
    ("8", ["exp8a", "exp8b"]), ("9", ["exp9"]), ("10", ["exp10a", "exp10b", "exp10c"]),
]
LABEL = {   # S = fault-specific tools, G = generic tools (3 calls), G6 = generic tools (6 calls)
    "baseline": "Native", "restart": "Naive restart", "rules": "Rules",
    "hybrid": "Hybrid", "llm-only": "LLM",
    "hybrid-tools": "Hybrid+S", "llm-tools": "LLM+S",
    "hybrid-generic": "Hybrid+G", "llm-generic": "LLM+G",
    "hybrid-generic-6": "Hybrid+G6", "llm-generic-6": "LLM+G6",
    "hybrid-14b": "Hybrid", "llm-only-14b": "LLM", "hybrid-tools-14b": "Hybrid+S",
    "hybrid-generic-14b": "Hybrid+G", "llm-generic-14b": "LLM+G",
    "hybrid-llama8b": "Hybrid", "llm-only-llama8b": "LLM",
    "hybrid-fe": "Hybrid+FE", "hybrid-fe-14b": "Hybrid+FE", "hybrid-fe-llama8b": "Hybrid+FE",
}
ORDER = ["baseline", "restart", "rules", "hybrid", "llm-only", "hybrid-tools", "llm-tools",
         "hybrid-generic", "llm-generic", "hybrid-generic-6", "llm-generic-6",
         "hybrid-14b", "llm-only-14b", "hybrid-tools-14b",
         "hybrid-generic-14b", "llm-generic-14b", "hybrid-llama8b", "llm-only-llama8b",
         "hybrid-fe", "hybrid-fe-14b", "hybrid-fe-llama8b"]
SHORT = {"oom-kill": "oom", "crashloop": "crash", "net-partition": "netpol", "bad-image": "image",
         "missing-config": "config", "pod-kill": "pod-kill", "transient-crash": "transient",
         "log-injection": "inject", "crash-release": "crash", "memory-release": "memory",
         "missing-secret": "secret$^h$", "unschedulable": "unsch.$^t$", "zero-replicas": "zero$^t$",
         "nonroot-release": "nonroot$^h$", "probe-misconfig": "probe$^h$"}
MARK = {"repaired": r"\ding{51}", "partial": r"$\circ$", "not repaired": r"\ding{55}"}


def model_of(cond: str) -> str:
    if cond in ("baseline", "restart", "rules"):
        return "--"
    if cond.endswith("llama8b"):
        return "L8B"
    return "14B" if cond.endswith("14b") else "7B"


def build(testbed: str):
    rows = []
    for exp, dirs in RUNS:
        dirs = [d for d in dirs if (R / d / "trials.csv").exists() and (R / d / "trials.csv").stat().st_size]
        if not dirs:
            continue
        x = load([str(R / d) for d in dirs], f"exp{exp}")
        if not len(x):
            continue
        x = x[x.testbed == testbed]
        if not len(x):
            continue
        fm = fault_matrix(x)
        fm["exp"] = exp
        rows.append(fm)
    fm = pd.concat(rows, ignore_index=True)
    all_conds = [(e, c) for e in dict.fromkeys(fm.exp) for c in ORDER
                 if ((fm.exp == e) & (fm.condition == c)).any()]

    def table(cell, caption_cols, exps=None):
        conds = [(e, c) for e, c in all_conds if exps is None or e in exps]
        present = set(fm[fm.exp.isin([e for e, _ in conds])].scenario)
        scen = [s for s in SCEN_ORDER[testbed] if s in present]
        lines = [r"\begin{tabular}{@{}lllc" + "c" * len(scen) + "c@{}}", r"\toprule",
                 "Exp. & Policy & Model & $n$ & " + " & ".join(SHORT[s] for s in scen) + f" & {caption_cols} " + r"\\",
                 r"\midrule"]
        prev = None
        for e, c in conds:
            sub = fm[(fm.exp == e) & (fm.condition == c)]
            if prev is not None and e != prev:
                lines.append(r"\addlinespace")
            cells = []
            for s in scen:
                r = sub[sub.scenario == s]
                cells.append(cell(r.iloc[0]) if len(r) else "")
            rep = f"{int((sub.verdict == 'repaired').sum())}/{len(sub)}"
            lines.append(f"{e if e != prev else ''} & {LABEL[c]} & {model_of(c)} & {int(sub.n.max())} & "
                         + " & ".join(cells)
                         + f" & {rep} " + r"\\")
            prev = e
        lines += [r"\bottomrule", r"\end{tabular}"]
        return "\n".join(lines) + "\n"

    T.mkdir(parents=True, exist_ok=True)
    (T / f"consolidated_{testbed}.tex").write_text(
        table(lambda r: f"{MARK[r.verdict]}{r.k}", "Faults"))
    (T / f"consolidated_ttr_{testbed}.tex").write_text(
        table(lambda r: "--" if np.isnan(r.ttr_med) else f"{r.ttr_med:.0f}", "Faults"))
    if testbed == "boutique":   # v6: split so that each half fits the page at a readable size
        early = {"3", "4", "5", "6"}
        late = set(fm.exp) - early
        for part, exps in (("a", early), ("b", late)):
            (T / f"consolidated_{testbed}_{part}.tex").write_text(
                table(lambda r: f"{MARK[r.verdict]}{r.k}", "Faults", exps))
            (T / f"consolidated_ttr_{testbed}_{part}.tex").write_text(
                table(lambda r: "--" if np.isnan(r.ttr_med) else f"{r.ttr_med:.0f}", "Faults", exps))
    print(f"{testbed}: {len(all_conds)} policy rows")


if __name__ == "__main__":
    for tb in ("demo", "boutique"):
        build(tb)
