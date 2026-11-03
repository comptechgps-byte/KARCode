#!/usr/bin/env bash
# v3 experiments (agent frozen at v3.0).
# Resumable: re-running skips completed trials. Log: results/v3_experiments.log
# Configuaration Developed BY Abhijeet Bajpayee
set -uo pipefail
cd "$(dirname "$0")/.."
B="python benchmark/run_benchmark.py --max-wait 300"

run() {   # run <out-dir> <args...> ; then apply the pre-specified checks and repeat flagged trials
  local out=$1; shift
  $B --out-dir "$out" "$@"
  python benchmark/suspend_check.py "$out" --apply --slack 180
  python benchmark/injection_check.py "$out" --apply
  $B --out-dir "$out" "$@"
}

bash benchmark/preflight.sh || { echo "PREFLIGHT FAILED"; exit 1; }

# Exp 4 — fault-agnostic generic tools (7B)
kubectl scale deploy --all -n boutique --replicas=0 >/dev/null 2>&1
run results/exp4a --testbed demo --conditions hybrid-generic,llm-generic --trials 5 --seed 20261002
run results/exp4b --testbed boutique --poll 1 --conditions hybrid-generic,llm-generic --trials 4 --seed 20261003

# Exp 5 — 14B completion (one model resident at a time)
kubectl scale deploy --all -n boutique --replicas=0 >/dev/null 2>&1
run results/exp5a --testbed demo --conditions hybrid-tools-14b --trials 5 --seed 20261004
run results/exp5b --testbed boutique --poll 1 --conditions hybrid-14b,hybrid-tools-14b --trials 4 --seed 20261005
echo "ALL V3 EXPERIMENTS DONE"
