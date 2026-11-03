#!/usr/bin/env bash
# v6 resume after kindnet lost NetworkPolicy enforcement during Exp 10b (all five
# net-partition trials invalid: fault never visible). preflight --fix was run first.
# The harness re-runs only the invalid trials of an existing run directory.
set -uo pipefail
cd "$(dirname "$0")/.."
B="python benchmark/run_benchmark.py --max-wait 300"
ARGS="--testbed demo --conditions hybrid-fe --scenarios oom-kill,crashloop,net-partition,pod-kill,transient-crash --trials 5 --seed 20261014"
$B --out-dir results/exp10b $ARGS
python benchmark/suspend_check.py results/exp10b --apply --slack 180
python benchmark/injection_check.py results/exp10b --apply
$B --out-dir results/exp10b $ARGS
echo "ALL V6 LIVE DONE"
URL=http://172.18.0.6:11434
for m in qwen2.5:3b-instruct llama3.2:3b qwen2.5:7b-instruct llama3.1:8b gemma2:9b qwen2.5:14b-instruct; do
  python benchmark/prompt_sensitivity.py --url $URL --model "$m" --set traps2
done
echo "ALL V6 DONE"
