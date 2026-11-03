#!/usr/bin/env python3
"""
paper_numbers.py — emits paper/results_macros.tex (every number quoted in the
text) and paper/tables/adversarial.tex from the result files, so that no
number in the manuscript is typed by hand.

    python benchmark/paper_numbers.py --main results/main --adv results/adversarial --paper paper
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.stats.proportion import proportion_confint

MODEL_LABEL = {"qwen2.5:7b-instruct": "Qwen2.5-7B", "llama3.1:8b": "Llama-3.1-8B",
               "qwen2.5:14b-instruct": "Qwen2.5-14B"}


def macro_name(*parts: str) -> str:
    """'hybrid','in-catalog','rate' -> HybridInCatalogRate (LaTeX-safe)."""
    words = []
    for p in parts:
        for w in str(p).replace("_", "-").replace(".", "-").split("-"):
            if w:
                words.append(w[0].upper() + w[1:])
    name = "".join(words)
    digits = {"0": "Zero", "1": "One", "2": "Two", "3": "Three", "4": "Four", "5": "Five",
              "6": "Six", "7": "Seven", "8": "Eight", "9": "Nine"}
    return "".join(digits.get(ch, ch) for ch in name)


def pct(x: float) -> str:
    return f"{100 * x:.0f}\\%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--main", default="results/main")
    ap.add_argument("--adv", default="results/adversarial")
    ap.add_argument("--paper", default="paper")
    args = ap.parse_args()
    main_dir, paper = Path(args.main), Path(args.paper)
    m: dict[str, str] = {}

    # ---------------- main experiment ---------------------------------
    raw = pd.read_csv(main_dir / "trials.csv")
    df = raw[raw.valid == True].drop_duplicates("trial_id", keep="last").copy()  # noqa: E712
    df["recovered"] = df.recovered.astype(str).str.lower().eq("true")
    m["NTrialsTotal"] = str(len(raw))
    m["NTrialsValid"] = str(len(df))
    m["NTrialsInvalid"] = str(int((raw.valid != True).sum()))  # noqa: E712
    m["NBlocks"] = str(df.block.max())

    summ = pd.read_csv(main_dir / "summary.csv")
    for _, r in summ.iterrows():
        k = (r.scenario, r.condition)
        m[macro_name(*k, "rec")] = f"{int(r.recovered)}/{int(r.n)}"
        m[macro_name(*k, "rate")] = pct(r.rate)
        m[macro_name(*k, "rmst")] = f"{r.rmst_s:.1f}"
        m[macro_name(*k, "med")] = "--" if pd.isna(r.km_median_s) else f"{r.km_median_s:.1f}"

    # pooled recovery per category x condition
    for (cat, cond), g in df.groupby(["category", "condition"]):
        k, n = int(g.recovered.sum()), len(g)
        lo, hi = proportion_confint(k, n, method="wilson")
        m[macro_name(cat, cond, "rec")] = f"{k}/{n}"
        m[macro_name(cat, cond, "rate")] = pct(k / n)
        m[macro_name(cat, cond, "ci")] = f"[{100*lo:.0f}, {100*hi:.0f}]"
        ttr = g.ttr_s[g.recovered]
        m[macro_name(cat, cond, "ttrmed")] = "--" if ttr.empty else f"{ttr.median():.1f}"

    # pooled: all faults, and faults native healing cannot fix (non self-healing)
    for label, sub in (("All", df), ("Persistent", df[df.category != "self-healing"])):
        for cond, g in sub.groupby("condition"):
            k, n = int(g.recovered.sum()), len(g)
            lo, hi = proportion_confint(k, n, method="wilson")
            m[macro_name(label, cond, "rec")] = f"{k}/{n}"
            m[macro_name(label, cond, "rate")] = pct(k / n)
            m[macro_name(label, cond, "ci")] = f"[{100*lo:.0f}, {100*hi:.0f}]"

    from scipy.stats import fisher_exact
    pers = df[df.category != "self-healing"]
    h = pers[pers.condition == "hybrid"].recovered
    for other in ("rules", "llm-only"):
        o = pers[pers.condition == other].recovered
        p = fisher_exact([[h.sum(), (~h).sum()], [o.sum(), (~o).sum()]])[1]
        m[macro_name("Persistent", "hybrid", "vs", other, "p")] = (
            "$p<0.001$" if p < 0.001 else f"$p={p:.3f}$")

    dec = pd.read_csv(main_dir / "decisions.csv")
    for _, r in dec.iterrows():
        k = (r.scenario, r.condition)
        m[macro_name(*k, "firstok")] = pct(r.first_action_correct)
        m[macro_name(*k, "unsafeprop")] = f"{int(r.trials_with_unsafe_proposal)}/{int(r.n)}"
        m[macro_name(*k, "unsafeexec")] = str(int(r.unsafe_executed))
        m[macro_name(*k, "actions")] = f"{r.actions_executed_mean:.1f}"
        m[macro_name(*k, "collateral")] = str(int(r.collateral_actions))
    m["UnsafeExecutedTotal"] = str(int(df.unsafe_executed.fillna(0).sum()))
    m["UnsafeProposalTrials"] = str(int((df.unsafe_proposals.fillna(0) > 0).sum()))
    m["BudgetRejectionsTotal"] = str(int(df.budget_rejections.fillna(0).sum()))
    m["CollateralTotal"] = str(int(df.collateral_actions.fillna(0).sum()))
    for cond, g in df.groupby("condition"):
        m[macro_name(cond, "collateral")] = str(int(g.collateral_actions.fillna(0).sum()))
        m[macro_name(cond, "budgetrej")] = str(int(g.budget_rejections.fillna(0).sum()))
        m[macro_name(cond, "llmcalls")] = str(int(g.n_llm_calls.fillna(0).sum()))

    ev_path = main_dir / "agent_events.jsonl"
    if ev_path.exists():
        from analyze_results import load_events
        ev = load_events([main_dir], df)
        lat = np.array([d["latency_s"] for d in ev.get("llm", pd.Series(dtype=object)).dropna()
                        if isinstance(d, dict) and d.get("latency_s")])
        if lat.size:
            m["LlmCalls"] = str(lat.size)
            m["LlmLatMed"] = f"{np.median(lat):.1f}"
            m["LlmLatPninetyfive"] = f"{np.percentile(lat, 95):.1f}"
            m["LlmLatMax"] = f"{lat.max():.1f}"
        errs = sum(1 for d in ev.get("llm", pd.Series(dtype=object)).dropna()
                   if isinstance(d, dict) and d.get("error"))
        m["LlmErrors"] = str(errs)

        # missing-config: diagnosis right, action wrong; rollback poisoning
        llm_ev = ev[ev.diagnosis_source == "llm"]
        mc = llm_ev[llm_ev.trial_id.str.contains("missing-config") & (llm_ev.deployment == "backend")]
        m["McLlmDiagnoses"] = str(len(mc))
        m["McLlmNamedConfig"] = str(int(mc.fault_class.str.lower().str.contains("config").sum()))
        m["McLlmRollbackProposals"] = str(int((mc.proposed_action == "rollback-deployment").sum()))
        rb = mc[(mc.proposed_action == "rollback-deployment") & (mc.outcome == "executed")]
        poisoned = 0
        for _, r in rb.iterrows():
            prior = ev[(ev.trial_id == r.trial_id) & (ev.deployment == "backend") & (ev.executed == True)  # noqa: E712
                       & (ev.t_acted < r.t_acted) & (ev.proposed_action != "rollback-deployment")]
            poisoned += int(len(prior) > 0)
        m["McRollbacksExecuted"] = str(len(rb))
        m["McRollbacksPoisoned"] = str(poisoned)
        # net-partition under llm-only: what the LLM did instead
        npl = llm_ev[llm_ev.trial_id.str.contains("net-partition") & (llm_ev.deployment == "cache-proxy")]
        m["NpLlmProposals"] = str(len(npl))
        m["NpLlmRestart"] = str(int((npl.proposed_action == "restart-pod").sum()))
        m["NpLlmRevert"] = str(int((npl.proposed_action == "revert-network-policy").sum()))
        # crashloop under llm-only: premature restarts before the rollback
        cl = llm_ev[llm_ev.trial_id.str.contains("-crashloop-") & (llm_ev.deployment == "backend")
                    & (llm_ev.outcome == "executed")]
        m["ClLlmRestartsExecuted"] = str(int((cl.proposed_action == "restart-pod").sum()))

    bd_path = main_dir / "latency_breakdown.csv"
    if bd_path.exists():
        bd = pd.read_csv(bd_path)
        for (s, c), g in bd.groupby(["scenario", "condition"]):
            for col in ("detect_s", "wait_s", "diagnose_s", "act_s", "converge_s"):
                m[macro_name(s, c, col.replace("_s", ""))] = f"{g[col].median():.1f}"

    # ---------------- adversarial --------------------------------------
    adv_files = sorted(Path(args.adv).glob("adversarial_*.csv"))
    if adv_files:
        adv = pd.concat([pd.read_csv(p) for p in adv_files], ignore_index=True)
        for col in ("hijacked", "unsafe", "gate_approved", "escaped", "prompt_warning"):
            adv[col] = adv[col].astype(str).str.lower().eq("true")
        # re-score with the current hijack criterion (single source of truth)
        from adversarial_eval import is_hijacked
        adv["hijacked"] = [is_hijacked(p, a, t) for p, a, t in zip(adv.payload, adv.action, adv.target.fillna(""))]
        attacks = adv[adv.payload != "none"]
        m["NAdvPayloads"] = str(attacks.payload.nunique())
        m["NAdvCalls"] = str(len(adv))
        m["AdvEscapedTotal"] = str(int(adv.escaped.sum()))
        m["AdvUnsafeTotal"] = str(int(attacks.unsafe.sum()))
        m["AdvAttackCalls"] = str(len(attacks))
        m["AdvHijackRate"] = pct(attacks.hijacked.mean())
        rows = []
        for (model, warn), g in attacks.groupby(["model", "prompt_warning"]):
            short = ("QwenLarge" if "14b" in model else "Qwen") if "qwen" in model else \
                "Llama" if "llama" in model else model
            key = (short, "warn" if warn else "nowarn")
            m[macro_name(*key, "hijack")] = pct(g.hijacked.mean())
            m[macro_name(*key, "unsafe")] = pct(g.unsafe.mean())
            rows.append((model, warn, g))
        ctrl = adv[adv.payload == "none"]
        m["AdvControlCorrect"] = f"{int((ctrl.action == 'rollback-deployment').sum())}/{len(ctrl)}"

        # table: payload x model, hijack counts with/without warning; unsafe; escaped
        models = list(dict.fromkeys(adv.model))
        lines = [r"\begin{tabular}{@{}l" + "cc" * len(models) + "c@{}}", r"\toprule",
                 "Payload & " + " & ".join(
                     f"\\multicolumn{{2}}{{c}}{{{MODEL_LABEL.get(mo, mo)}}}" for mo in models)
                 + r" & Escaped \\",
                 "".join(f"\\cmidrule(lr){{{2+2*i}-{3+2*i}}}" for i in range(len(models))),
                 " & " + " & ".join(["warn & no warn"] * len(models)) + r" & \\", r"\midrule"]
        for pid in dict.fromkeys(adv.payload):
            cells = []
            for mo in models:
                for warn in (True, False):
                    g = adv[(adv.model == mo) & (adv.payload == pid) & (adv.prompt_warning == warn)]
                    if pid == "none":
                        cells.append(f"{int((g.action == 'rollback-deployment').sum())}/{len(g)}$^\\ast$")
                    else:
                        cells.append(f"{int(g.hijacked.sum())}/{len(g)}")
            esc = int(adv[adv.payload == pid].escaped.sum())
            lines.append(f"\\code{{{pid}}} & " + " & ".join(cells) + f" & {esc} \\\\")
        tot = []
        for mo in models:
            for warn in (True, False):
                g = attacks[(attacks.model == mo) & (attacks.prompt_warning == warn)]
                tot.append(f"{int(g.hijacked.sum())}/{len(g)}")
        lines += [r"\midrule", "All attacks & " + " & ".join(tot) + f" & {int(attacks.escaped.sum())} \\\\",
                  r"\bottomrule", r"\end{tabular}"]
        (paper / "tables").mkdir(parents=True, exist_ok=True)
        (paper / "tables" / "adversarial.tex").write_text("\n".join(lines) + "\n")

        # gate decision breakdown for unsafe proposals
        uns = attacks[attacks.unsafe]
        for chk, g in uns.groupby("gate_check"):
            m[macro_name("AdvBlocked", chk)] = str(len(g))
        # in-scope, non-destructive but payload-directed actions that the gate allows
        allowed_hijacks = attacks[attacks.hijacked & ~attacks.unsafe & attacks.gate_approved]
        m["AdvAllowedHijacks"] = str(len(allowed_hijacks))

    out = ["% generated by benchmark/paper_numbers.py — do not edit by hand"]
    out += [f"\\newcommand{{\\{k}}}{{{v}}}" for k, v in sorted(m.items())]
    (paper / "results_macros.tex").write_text("\n".join(out) + "\n")
    print(f"wrote {len(m)} macros to {paper / 'results_macros.tex'}")


if __name__ == "__main__":
    main()
