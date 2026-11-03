#!/usr/bin/env python3
"""
paper_numbers_v4.py — macros for manuscript v4: Experiment 6 (generic tools with
a 6-call budget), the replication of Experiment 2a, the cross-family prompt
study and the 14B offline injection study. Writes paper/results_macros_v4.tex
and paper/tables/prompt_families.tex. Tolerates studies that have not run yet.

    python benchmark/paper_numbers_v4.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_results import load_events  # noqa: E402
from analyze_v2 import fault_matrix, load  # noqa: E402
from paper_numbers import macro_name, pct  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
R, P = ROOT / "results", ROOT / "paper"
FAULTS = ["oom-kill", "crashloop", "net-partition", "bad-image", "missing-config"]
FAMILY = {  # file stem in results/prompt_sensitivity -> (label, family, size order)
    "qwen2.5_3b-instruct": ("Qwen2.5 3B", 0), "llama3.2_3b": ("Llama-3.2 3B", 1),
    "qwen2.5_7b-instruct": ("Qwen2.5 7B", 2), "llama3.1_8b": ("Llama-3.1 8B", 3),
    "gemma2_9b": ("Gemma-2 9B", 4), "qwen2.5_14b-instruct": ("Qwen2.5 14B", 5),
}


def nonempty(*dirs: str) -> list[str]:
    return [str(R / d) for d in dirs if (R / d / "trials.csv").exists() and (R / d / "trials.csv").stat().st_size]


def llm_events(x: pd.DataFrame, dirs: list[str]) -> pd.DataFrame:
    parts = []
    for d in dirs:
        xd = x[x.run_dir == d]
        if len(xd):
            e = load_events([d], xd)
            if len(e):
                e = e.copy()
                e["cond"] = e.trial_id.map(xd.set_index("trial_id").condition)
                parts.append(e)
    if not parts:
        return pd.DataFrame(columns=["diagnosis_source", "trial_id", "llm", "cond"])
    ev = pd.concat(parts, ignore_index=True)
    return ev[ev.diagnosis_source == "llm"]


def exp6(m: dict) -> None:
    dirs = nonempty("exp6a", "exp6b")
    if not dirs:
        return
    x = load(dirs, "exp6")
    m["NTrialsExpSix"] = str(len(x))
    fm = fault_matrix(x)
    for _, r in fm.iterrows():
        k = ("ExpSix", r.testbed, r.scenario, r.condition)
        m[macro_name(*k, "rec")] = f"{r.k}/{r.n}"
        m[macro_name(*k, "ttr")] = "--" if np.isnan(r.ttr_med) else f"{r.ttr_med:.0f}"
    llm = llm_events(x, dirs)
    calls = [t for d in llm.llm if isinstance(d, dict) for t in d.get("tools", [])]
    n_steps = [len(d.get("tools", [])) for d in llm.llm if isinstance(d, dict)]
    m["ExpSixDiagnoses"] = str(len(llm))
    m["ExpSixToolCalls"] = str(len(calls))
    m["ExpSixMeanCalls"] = f"{np.mean(n_steps):.1f}" if n_steps else "--"
    m["ExpSixUsedMoreThanThree"] = pct(np.mean([s > 3 for s in n_steps])) if n_steps else "--"
    seqs = [d.get("tools", []) for d in llm.llm if isinstance(d, dict)]
    repeats = sum(sum(1 for i, t in enumerate(s) if t in s[:i]) for s in seqs)
    m["ExpSixRepeatedCallShare"] = pct(repeats / max(1, len(calls)))
    m["ExpSixFullBudgetShare"] = pct(np.mean([len(s) >= 6 for s in seqs])) if seqs else "--"
    np_diag = llm[llm.trial_id.str.contains("net-partition")]
    np_calls = [t for d in np_diag.llm if isinstance(d, dict) for t in d.get("tools", [])]
    m["ExpSixNetPartDiagnoses"] = str(len(np_diag))
    m["ExpSixNetPartNetpolLookups"] = str(sum("networkpolicies" in t for t in np_calls))
    lat = [d.get("latency_s") for d in llm.llm if isinstance(d, dict) and d.get("latency_s")]
    m["ExpSixLlmlat"] = f"{np.median(lat):.0f}" if lat else "--"
    m["ExpSixUnsafeExecuted"] = str(int(x.unsafe_executed.fillna(0).sum()))
    # what did the model propose for the faults it failed?
    for fault in ("net-partition", "log-injection", "missing-secret"):
        g = llm[llm.trial_id.str.contains(fault)]
        acts = g.proposed_action.dropna().value_counts()
        if len(acts):
            m[macro_name("ExpSix", fault, "topaction")] = acts.index[0].replace("-", "\\mbox{-}")
            m[macro_name("ExpSix", fault, "topactionshare")] = pct(acts.iloc[0] / acts.sum())


def replication(m: dict) -> None:
    dirs = nonempty("exp2a_replication")
    if not dirs:
        return
    rep = load(dirs, "exp2a-R")
    orig = load([str(R / "exp2a")], "exp2a")
    m["NTrialsReplication"] = str(len(rep))
    fr, fo = fault_matrix(rep), fault_matrix(orig)
    j = fo.merge(fr, on=["testbed", "scenario", "condition"], suffixes=("_o", "_r"))
    m["ReplicationPairs"] = str(len(j))
    agree = (j.verdict_o == j.verdict_r)
    m["ReplicationAgree"] = str(int(agree.sum()))
    m["ReplicationAgreeShare"] = pct(agree.mean())
    dis = j[~agree]
    m["ReplicationDisagreements"] = "; ".join(
        f"\\code{{{r.scenario}}}/{r.condition}: {r.k_o}/{r.n_o} vs {r.k_r}/{r.n_r}" for _, r in dis.iterrows()) or "none"
    # per-policy fault-type counts in both runs
    for cond, g in j.groupby("condition"):
        m[macro_name("Replication", cond, "orig")] = f"{int((g.verdict_o == 'repaired').sum())}/{len(g)}"
        m[macro_name("Replication", cond, "rep")] = f"{int((g.verdict_r == 'repaired').sum())}/{len(g)}"
    # timing concordance: median TTR of cells repaired in both runs
    both = j[(j.verdict_o == "repaired") & (j.verdict_r == "repaired")]
    if len(both):
        d = (both.ttr_med_r - both.ttr_med_o).abs()
        m["ReplicationTtrMedAbsDiff"] = f"{d.median():.0f}"
        m["ReplicationTtrMaxAbsDiff"] = f"{d.max():.0f}"
        m["ReplicationTtrCorr"] = f"{np.corrcoef(both.ttr_med_o, both.ttr_med_r)[0, 1]:.2f}" if len(both) > 2 else "--"
    m["ReplicationUnsafeExecuted"] = str(int(rep.unsafe_executed.fillna(0).sum()))


def prompt_families(m: dict) -> None:
    frames = []
    for f in sorted((R / "prompt_sensitivity").glob("*.csv")):
        if f.stem not in FAMILY:
            continue
        d = pd.read_csv(f)
        d["model"] = f.stem
        frames.append(d)
    if not frames:
        return
    d = pd.concat(frames)
    d["correct"] = d.correct.astype(str).str.lower().eq("true")
    models = sorted(set(d.model), key=lambda s: FAMILY[s][1])
    lines = [r"\begin{tabular}{@{}l" + "c" * len(FAULTS) + "ccc@{}}", r"\toprule",
             "Model & " + " & ".join(["oom", "crash", "netpol", "image", "config"])
             + r" & Consistent & Stable & Correct \\",
             r"\midrule"]
    for mm in models:
        cells, stable, consistent = [], 0, 0
        for fault in FAULTS:
            g = d[(d.model == mm) & (d.fault == fault) & (d.decoding == "greedy")]
            k = int(g.correct.sum())
            cells.append(f"{k}/{len(g)}")
            stable += int(len(g) > 0 and k == len(g))
            consistent += int(g.action.nunique() == 1)
        allg = d[(d.model == mm)]
        lines.append(f"{FAMILY[mm][0]} & " + " & ".join(cells)
                     + f" & {consistent}/{len(FAULTS)} & {stable}/{len(FAULTS)} & {pct(allg.correct.mean())} " + r"\\")
        m[macro_name("Family", mm, "stable")] = str(stable)
        m[macro_name("Family", mm, "consistent")] = str(consistent)
        m[macro_name("Family", mm, "correct")] = pct(allg.correct.mean())
    lines += [r"\bottomrule", r"\end{tabular}"]
    (P / "tables" / "prompt_families.tex").write_text("\n".join(lines) + "\n")
    m["FamilyModels"] = str(len(models))
    m["FamilyCalls"] = str(len(d))


def adversarial_14b(m: dict) -> None:
    f = R / "adversarial" / "adversarial_qwen2.5_14b-instruct.csv"
    if not f.exists():
        return
    d = pd.read_csv(f)
    for c in ("prompt_warning", "hijacked", "unsafe", "escaped"):
        d[c] = d[c].astype(str).str.lower().eq("true")
    att = d[d.payload != "none"]
    for warn, g in att.groupby("prompt_warning"):
        m[f"AdvOneFourB{'Warn' if warn else 'Nowarn'}Hijack"] = pct(g.hijacked.mean())
    ctl = d[d.payload == "none"]
    m["AdvOneFourBControlCorrect"] = f"{int((ctl.action == 'rollback-deployment').sum())}/{len(ctl)}"
    m["AdvOneFourBUnsafe"] = str(int(att.unsafe.sum()))
    m["AdvOneFourBEscaped"] = str(int(att.escaped.sum()))
    m["AdvOneFourBCalls"] = str(len(d))


def main():
    m: dict[str, str] = {}
    for step in (exp6, replication, prompt_families, adversarial_14b):
        try:
            step(m)
        except Exception as e:  # keep the other studies' macros if one is incomplete
            print(f"warning: {step.__name__}: {e}")
    all_dirs = nonempty("main", "exp2a", "exp2b", "exp3", "exp4a", "exp4b", "exp5a", "exp5b",
                        "exp6a", "exp6b", "exp2a_replication")
    total = sum(len(load([d], "all")) for d in all_dirs)
    m["NTrialsAllExperimentsVfour"] = f"{total:,}".replace(",", "{,}")
    for k in ("NTrialsExpSix", "NTrialsReplication"):
        m.setdefault(k, "0")
    out = P / "results_macros_v4.tex"
    lines = ["% generated by benchmark/paper_numbers_v4.py — do not edit by hand"]
    lines += [f"\\newcommand{{\\{k}}}{{{v}}}" for k, v in sorted(m.items()) if re.fullmatch(r"[A-Za-z]+", k)]
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {len(lines) - 1} macros to {out}")


if __name__ == "__main__":
    main()
