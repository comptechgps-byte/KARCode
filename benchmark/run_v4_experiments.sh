#!/usr/bin/env bash
# Setup Code Logic developed By Abhijeet bAJPAYEE v4 experiments (agent unchanged: v3.0).
#   Exp 6 — step-limit ablation: generic tools with 6 instead of 3 tool calls,
#           on the faults the generic modes failed (+ missing-config as control).
#   Exp 2a-R — replication of Experiment 2a (same conditions, same seed), which
#           also settles whether keeping the post-reboot Exp 2a trials was safe.
set -uo pipefail
cd "$(dirname "$0")/.."
B="python benchmark/run_benchmark.py --max-wait 300"
run() {
  local out=$1; shift
  $B --out-dir "$out" "$@"
  python benchmark/suspend_check.py "$out" --apply --slack 180
  python benchmark/injection_check.py "$out" --apply
  $B --out-dir "$out" "$@"
}
bash benchmark/preflight.sh --fix || { echo "PREFLIGHT FAILED"; exit 1; }

kubectl scale deploy --all -n boutique --replicas=0 >/dev/null 2>&1
run results/exp6a --testbed demo --conditions llm-generic-6 \
    --scenarios net-partition,log-injection,missing-config --trials 5 --seed 20261006
run results/exp6b --testbed boutique --poll 1 --conditions llm-generic-6,hybrid-generic-6 \
    --scenarios missing-secret --trials 4 --seed 20261007
echo "EXP6 DONE"

kubectl scale deploy --all -n boutique --replicas=0 >/dev/null 2>&1
bash benchmark/preflight.sh --fix || { echo "PREFLIGHT FAILED (before replication)"; exit 1; }
run results/exp2a_replication --testbed demo --conditions hybrid,llm-only,hybrid-tools,llm-tools \
    --trials 5 --seed 20260930
echo "ALL V4 EXPERIMENTS DONE"
