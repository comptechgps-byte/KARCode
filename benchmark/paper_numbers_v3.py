#!/usr/bin/env python3
"""
paper_numbers_v3.py — macros and tables for manuscript v3: prompt sensitivity
(offline), Experiment 4 (generic tools) and Experiment 5 (14B completion).
Writes paper/results_macros_v3.tex and paper/tables/{prompt_sensitivity,
matrix_exp4,matrix_exp5}.tex. Tolerates experiments that have not run yet.

    python benchmark/paper_numbers_v3.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_results import load_events  # noqa: E402
from analyze_v2 import COND_LABEL, fault_matrix, load, tex_matrix  # noqa: E402
from paper_numbers import macro_name, pct  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
R, P = ROOT / "results", ROOT / "paper"
COND_LABEL.update({"hybrid-generic": "Hybrid+generic", "llm-generic": "LLM+generic"})
MODELS = {"qwen2.5_7b-instruct": "7B", "qwen2.5_14b-instruct": "14B"}
PROMPT_LABEL = {"original": "Original", "paraphrase-a": "Paraphrase A", "paraphrase-b": "Paraphrase B"}
FAULTS = ["oom-kill", "crashloop", "net-partition", "bad-image", "missing-config"]


def prompt_table(m: dict) -> None:
    frames = []
    for f in sorted((R / "prompt_sensitivity").glob("*.csv")):
        d = pd.read_csv(f)
        d["model"] = MODELS.get(f.stem, f.stem)
        frames.append(d)
    if not frames:
        return
    d = pd.concat(frames)
    d["correct"] = d.correct.astype(str).str.lower().eq("true")
    models = [mm for mm in ("7B", "14B") if mm in set(d.model)]
    prompts = list(PROMPT_LABEL)
    head = " & ".join(f"\\multicolumn{{3}}{{c}}{{{mm}}}" for mm in models)
    rules = "".join(f"\\cmidrule(lr){{{2 + 3 * i}-{4 + 3 * i}}}" for i in range(len(models)))
    sub = " & ".join(["Orig. & A & B"] * len(models))
    lines = [r"\begin{tabular}{@{}l" + "ccc" * len(models) + "@{}}", r"\toprule",
             f"Fault & {head} \\\\", rules, f" & {sub} \\\\", r"\midrule"]
    for fault in FAULTS:
        cells = []
        for mm in models:
            for p in prompts:
                g = d[(d.model == mm) & (d.fault == fault) & (d.prompt == p)]
                greedy_ok = bool(g[g.decoding == "greedy"].correct.all())
                cells.append(f"{int(g.correct.sum())}/{len(g)}" + ("" if greedy_ok else r"$^\dagger$"))
        lines.append(f"\\code{{{fault}}} & " + " & ".join(cells) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    (P / "tables" / "prompt_sensitivity.tex").write_text("\n".join(lines) + "\n")
    for mm in models:
        g = d[(d.model == mm) & (d.decoding == "greedy")]
        stable = g.groupby("fault").correct.all()
        m[macro_name("Prompt", mm, "stable")] = f"{int(stable.sum())}"
        m[macro_name("Prompt", mm, "calls")] = str(len(d[d.model == mm]))
    m["PromptFaults"] = str(len(FAULTS))


def main():
    m: dict[str, str] = {}
    prompt_table(m)

    e4 = load([str(R / "exp4a"), str(R / "exp4b")], "exp4")
    e5 = load([str(R / "exp5a"), str(R / "exp5b")], "exp5")
    for exp, x, dirs, conds in (
            ("exp4", e4, [R / "exp4a", R / "exp4b"], ["hybrid-generic", "llm-generic"]),
            ("exp5", e5, [R / "exp5a", R / "exp5b"], ["hybrid-14b", "hybrid-tools-14b"])):
        if not len(x):
            continue
        fm = fault_matrix(x)
        m[macro_name("NTrials", exp)] = str(len(x))
        for (tb, cond), g in fm.groupby(["testbed", "condition"]):
            m[macro_name(exp, tb, cond, "repaired")] = f"{int((g.verdict == 'repaired').sum())}/{len(g)}"
        for _, r in fm.iterrows():
            k = (exp, r.testbed, r.scenario, r.condition)
            m[macro_name(*k, "rec")] = f"{r.k}/{r.n}"
            m[macro_name(*k, "ttr")] = "--" if np.isnan(r.ttr_med) else f"{r.ttr_med:.0f}"
        for tb in sorted(set(fm.testbed)):
            tex_matrix(fm, tb, conds, P / "tables" / f"matrix_{exp}_{tb}.tex")
        # trial ids repeat across testbeds (e.g. pod-kill), so match events per run directory
        parts = []
        for d in dirs:
            xd = x[x.run_dir == str(d)]
            if len(xd):
                e = load_events([str(d)], xd)
                if len(e):
                    e = e.copy()
                    e["cond"] = e.trial_id.map(xd.set_index("trial_id").condition)
                    e["tb"] = xd.testbed.iloc[0]
                    parts.append(e)
        ev = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
            columns=["diagnosis_source", "trial_id", "llm", "cond", "tb"])
        llm = ev[ev.diagnosis_source == "llm"].copy()
        for (tb, cond), g in llm.groupby(["tb", "cond"]):
            lat = [d.get("latency_s") for d in g.llm if isinstance(d, dict) and d.get("latency_s")]
            if lat:
                m[macro_name(exp, tb, cond, "llmlat")] = f"{np.median(lat):.0f}"
        if exp == "exp4":   # which kinds did the model inspect with the generic tools?
            calls = [t for d in llm.llm if isinstance(d, dict) for t in d.get("tools", [])]
            s = pd.Series(calls)
            m["ExpFourToolCalls"] = str(len(s))
            m["ExpFourDiagnoses"] = str(len(llm))
            kinds = s.str.split(":").str[1].dropna()
            for kind, share in (kinds.value_counts(normalize=True)).items():
                m[macro_name("ExpFourKindShare", kind)] = pct(share)
            m["ExpFourKindsUsed"] = str(kinds.nunique())
            np_diag = llm[llm.trial_id.str.contains("net-partition")]
            np_calls = [t for d in np_diag.llm if isinstance(d, dict) for t in d.get("tools", [])]
            m["ExpFourNetPartDiagnoses"] = str(len(np_diag))
            m["ExpFourNetPartNetpolLookups"] = str(sum("networkpolicies" in t for t in np_calls))
        m[macro_name(exp, "UnsafeExecuted")] = str(int(x.unsafe_executed.fillna(0).sum()))

    # combined v3 tables per testbed: Exp 4 (generic, 7B) next to Exp 5 (14B)
    both = pd.concat([x for x in (e4, e5) if len(x)], ignore_index=True)
    if len(both):
        fmb = fault_matrix(both)
        tex_matrix(fmb, "demo", ["hybrid-generic", "llm-generic", "hybrid-tools-14b"],
                   P / "tables" / "matrix_v3_demo.tex")
        tex_matrix(fmb, "boutique", ["hybrid-generic", "llm-generic", "hybrid-14b", "hybrid-tools-14b"],
                   P / "tables" / "matrix_v3_boutique.tex")
    # grand total over all experiments
    e1 = load([str(R / "main")], "exp1")
    e2 = load([str(R / "exp2a"), str(R / "exp2b")], "exp2")
    e3 = load([str(R / "exp3")], "exp3")
    total = sum(len(x) for x in (e1, e2, e3, e4, e5))
    m["NTrialsAllExperimentsVthree"] = f"{total:,}".replace(",", "{,}")
    m["NTrialsExpFour"] = str(len(e4))
    m["NTrialsExpFive"] = str(len(e5))

    out = P / "results_macros_v3.tex"
    lines = ["% generated by benchmark/paper_numbers_v3.py — do not edit by hand"]
    lines += [f"\\newcommand{{\\{k}}}{{{v}}}" for k, v in sorted(m.items()) if re.fullmatch(r"[A-Za-z]+", k)]
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {len(lines) - 1} macros to {out}")


if __name__ == "__main__":
    main()
