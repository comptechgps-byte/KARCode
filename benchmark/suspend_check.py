#!/usr/bin/env python3
"""
suspend_check.py — detect trials overlapped by a host suspension (laptop
sleep pauses Docker, so wall-clock time jumps inside a trial and its TTR or
censoring is wrong).

A trial's wall-clock span (next trial's t0 minus its t0) is compared with what
it should take: TTR (or the censoring window) + stability window + reset and
agent-start overhead. Trials whose excess exceeds --slack seconds are marked
valid=False with a note, so that re-running run_benchmark.py with the same
--out-dir repeats exactly those trials.

    python benchmark/suspend_check.py results/main            # report only
    python benchmark/suspend_check.py results/main --apply    # mark invalid
"""
import argparse
from pathlib import Path

import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("run_dir")
ap.add_argument("--stable", type=float, default=10)
ap.add_argument("--overhead", type=float, default=60, help="generous reset + agent start + collection")
ap.add_argument("--slack", type=float, default=120)
ap.add_argument("--apply", action="store_true")
args = ap.parse_args()

p = Path(args.run_dir) / "trials.csv"
df = pd.read_csv(p)
t0 = pd.to_datetime(df.t0_utc, format="ISO8601")
span = (t0.shift(-1) - t0).dt.total_seconds()
expected = df.ttr_s.fillna(0) + args.stable + args.overhead
df["excess_s"] = (span - expected).round(0)
bad = df[(df.excess_s > args.slack) & (df.valid == True)]  # noqa: E712
print(bad[["trial_id", "t0_utc", "ttr_s", "recovered", "excess_s"]].to_string(index=False)
      if len(bad) else "no suspended trials")
if args.apply and len(bad):
    df.loc[bad.index, "valid"] = False
    df.loc[bad.index, "note"] = "host suspended during trial (excess wall-clock %.0fs); re-run"
    df.loc[bad.index, "note"] = [n % e for n, e in zip(df.loc[bad.index, "note"], bad.excess_s)]
    df.drop(columns="excess_s").to_csv(p, index=False)
    print(f"marked {len(bad)} trials invalid in {p}")
