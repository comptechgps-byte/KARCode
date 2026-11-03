#!/usr/bin/env python3
"""
paper_numbers_v2.py — macros for manuscript v2 (Experiments 2 and 3 and the
fault-type-level summary), written to paper/results_macros_v2.tex.

    python benchmark/paper_numbers_v2.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_results import load_events  # noqa: E402
from analyze_v2 import decisive_tiers, fault_matrix, load  # noqa: E402
from paper_numbers import macro_name, pct  # noqa: E402

R = Path(__file__).resolve().parent.parent / "results"
EXP2 = [str(R / "exp2a"), str(R / "exp2b")]
EXP3 = [str(R / "exp3")]
EXP1 = [str(R / "main")]


def main():
    m: dict[str, str] = {}
    e1, e2, e3 = load(EXP1, "exp1"), load(EXP2, "exp2"), load(EXP3, "exp3")
    allx = pd.concat([e1, e2, e3], ignore_index=True)
    fm = fault_matrix(allx)

    n_types = e1.scenario.nunique() + e3.scenario.nunique()
    m["NFaultTypesTotal"] = str(n_types)
    m["NHeldOut"] = str(e3[e3.category == "held-out"].scenario.nunique())
    m["NTrialsExpTwo"] = str(len(e2))
    m["NTrialsExpTwoA"] = str(len(e2[e2.run_dir.str.endswith("exp2a")]))
    m["NTrialsExpTwoB"] = str(len(e2[e2.run_dir.str.endswith("exp2b")]))
    m["NTrialsExpThree"] = str(len(e3))
    m["NTrialsAllExperiments"] = f"{len(e1) + len(e2) + len(e3):,}".replace(",", "{,}")

    # fault types repaired (all trials) per experiment x condition
    for exp, sub in fm.groupby("experiment"):
        for cond, g in sub.groupby("condition"):
            m[macro_name(exp, cond, "repaired")] = f"{int((g.verdict == 'repaired').sum())}/{len(g)}"
            m[macro_name(exp, cond, "partial")] = str(int((g.verdict == "partial").sum()))
    # per cell (experiment, scenario, condition): k/n and median TTR
    for _, r in fm.iterrows():
        k = (r.experiment, r.scenario, r.condition)
        m[macro_name(*k, "rec")] = f"{r.k}/{r.n}"
        m[macro_name(*k, "ttr")] = "--" if np.isnan(r.ttr_med) else f"{r.ttr_med:.0f}"
        if not np.isnan(r.user_down_med):
            m[macro_name(*k, "down")] = f"{r.user_down_med:.0f}"

    # LLM latency per condition (exp2, exp3)
    for exp, x, dirs in (("exp2", e2, EXP2), ("exp3", e3, EXP3)):
        ev = load_events(dirs, x)
        llm = ev[ev.diagnosis_source == "llm"].copy()
        llm["cond"] = llm.trial_id.map(x.set_index("trial_id").condition)
        for cond, g in llm.groupby("cond"):
            lat = [d.get("latency_s") for d in g.llm if isinstance(d, dict) and d.get("latency_s")]
            if lat:
                m[macro_name(exp, cond, "llmlat")] = f"{np.median(lat):.0f}"
        # tool-use pattern
        tl = llm[llm.cond.astype(str).str.contains("tools")]
        seqs = [tuple(d.get("tools", [])) for d in tl.llm if isinstance(d, dict)]
        if seqs:
            top = pd.Series(seqs).value_counts()
            m[macro_name(exp, "ToolDiagnoses")] = str(len(seqs))
            m[macro_name(exp, "ToolTopSeqShare")] = pct(top.iloc[0] / len(seqs))
            m[macro_name(exp, "ToolTopSeq")] = ", ".join(r"\code{" + t.replace("_", r"\_") + "}" for t in top.index[0])
            m[macro_name(exp, "ToolCheckConfigCalls")] = str(sum(s.count("check_config_references") for s in seqs))
        # rollbacks that targeted the last observed healthy revision
        rb = ev[(ev.proposed_action == "rollback-deployment") & (ev.executed == True)]  # noqa: E712
        m[macro_name(exp, "RollbacksExecuted")] = str(len(rb))
        m[macro_name(exp, "RollbacksLastHealthy")] = str(int(rb.action_detail.astype(str).str.contains("last observed healthy").sum()))
        m[macro_name(exp, "UnsafeExecuted")] = str(int(x.unsafe_executed.fillna(0).sum()))
        m[macro_name(exp, "CollateralHybrid")] = str(int(x[x.condition.str.startswith("hybrid")].collateral_actions.fillna(0).sum()))

    # decisive action tiers (human-approval sensitivity)
    tiers = []
    for x, dirs in ((e1, EXP1), (e2, EXP2), (e3, EXP3)):
        t = decisive_tiers(x, dirs)
        t["experiment"] = x.experiment.iloc[0]
        tiers.append(t)
    tiers = pd.concat(tiers, ignore_index=True)
    acted = tiers[tiers.tier != "none (self-healed)"]
    m["DecisiveActed"] = str(len(acted))
    m["DecisiveModerate"] = str(int((acted.tier == "moderate").sum()))
    m["DecisiveModerateShare"] = pct((acted.tier == "moderate").mean())

    # Exp 3 user downtime: masked releases vs visible faults
    rel = e3[e3.scenario.isin(["bad-image", "crash-release", "memory-release", "missing-secret", "unschedulable"])]
    m["ExpThreeReleaseUserDownMed"] = f"{rel.user_down_s.median():.0f}"
    m["ExpThreeReleaseUserDownMax"] = f"{rel.user_down_s.max():.0f}"
    repairing = rel[rel.condition.isin(["rules", "hybrid", "hybrid-tools", "llm-tools"])]
    m["ExpThreeReleaseRepairDownMax"] = f"{repairing.user_down_s.max():.0f}"
    m["ExpThreeReleaseRepairDownTrials"] = str(int((repairing.user_down_s > 5).sum()))
    m["ExpThreeReleaseRepairTrials"] = str(len(repairing))
    m["ExpThreeReleaseRestartDownMax"] = f"{rel[rel.condition == 'restart'].user_down_s.max():.0f}"
    mr = rel[(rel.scenario == "memory-release") & (rel.condition == "rules")]
    m["ExpThreeMemoryReleaseRulesDownMed"] = f"{mr.user_down_s.median():.0f}"
    zr = e3[e3.scenario == "zero-replicas"]
    m["ExpThreeZeroReplicasDownMed"] = f"{zr.user_down_s.median():.0f}"
    pk = e3[e3.scenario == "pod-kill"]
    for cond, g in pk.groupby("condition"):
        m[macro_name("ExpThreePodKillDown", cond)] = f"{g.user_down_s.median():.0f}"

    out = Path(__file__).resolve().parent.parent / "paper" / "results_macros_v2.tex"
    lines = ["% generated by benchmark/paper_numbers_v2.py — do not edit by hand"]
    lines += [f"\\newcommand{{\\{k}}}{{{v}}}" for k, v in sorted(m.items()) if re.fullmatch(r"[A-Za-z]+", k)]
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {len(lines) - 1} macros to {out}")


if __name__ == "__main__":
    main()
