#!/usr/bin/env bash
# v6 experiments — agent v3.1 (failure-triggered escalation).
#   Exp 10a — the rule-misfire fault of Exp 9 (probe-misconfig) under failure-triggered
#             escalation, three models
#   Exp 10b/c — regression: rule-covered and self-healing faults must behave as before
# Then the offline rollback-trap extension for all six models.
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

kubectl scale deploy --all -n agentic-demo --replicas=0 >/dev/null 2>&1
run results/exp10a --testbed boutique --poll 1 --conditions hybrid-fe,hybrid-fe-14b,hybrid-fe-llama8b \
    --scenarios probe-misconfig --trials 4 --seed 20261013
run results/exp10c --testbed boutique --poll 1 --conditions hybrid-fe \
    --scenarios crash-release,memory-release,pod-kill --trials 4 --seed 20261015
echo "BOUTIQUE DONE"

kubectl scale deploy --all -n boutique --replicas=0 >/dev/null 2>&1
run results/exp10b --testbed demo --conditions hybrid-fe \
    --scenarios oom-kill,crashloop,net-partition,pod-kill,transient-crash --trials 5 --seed 20261014
echo "ALL V6 LIVE DONE"

URL=http://172.18.0.6:11434
for m in qwen2.5:3b-instruct llama3.2:3b qwen2.5:7b-instruct llama3.1:8b gemma2:9b qwen2.5:14b-instruct; do
  python benchmark/prompt_sensitivity.py --url $URL --model "$m" --set traps2
done
echo "ALL V6 DONE"
