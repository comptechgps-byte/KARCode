#!/usr/bin/env python3
"""
Developed By Abhijeet Bajpayee
injection_check.py — apply the pre-specified injection-failure rule to an
existing run: a trial whose fault did not become visible to the independent
health check within 60 s is an injection failure, not an outcome of the
policy under test. Such trials are marked valid=False so that re-running
run_benchmark.py with the same --out-dir repeats them.

    python benchmark/injection_check.py results/exp2b            # report
    python benchmark/injection_check.py results/exp2b --apply    # mark
"""
import argparse
from pathlib import Path

import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("run_dir")
ap.add_argument("--limit", type=float, default=60)
ap.add_argument("--apply", action="store_true")
args = ap.parse_args()

p = Path(args.run_dir) / "trials.csv"
df = pd.read_csv(p)
df["note"] = df["note"].astype(object)
vis = pd.to_numeric(df.t_fault_visible_s, errors="coerce")
bad = df[(df.valid.astype(str) == "True") & (vis.isna() | (vis > args.limit))]
print(f"{p}: {len(bad)} injection failures")
if len(bad):
    print(bad[["trial_id", "t_fault_visible_s", "ttr_s", "recovered"]].to_string(index=False))
if args.apply and len(bad):
    df.loc[bad.index, "valid"] = False
    df.loc[bad.index, "note"] = [f"injection failure: fault not visible within {args.limit:.0f} s "
                                 f"(visible at {v})" for v in bad.t_fault_visible_s]
    df.to_csv(p, index=False)
    print(f"marked {len(bad)} trials invalid")
