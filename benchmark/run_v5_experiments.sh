#!/usr/bin/env bash
# v5 experiments (agent unchanged: v3.0; harness
# adds a model warm-up before injection).
#   Exp 7 — 14B with generic tools (completes tools x capability)
#   Exp 8 — a second model family live: Llama-3.1-8B
#   Exp 9 — two more held-out Boutique faults that make a pod unhealthy
# Then the offline rollback-trap study for all six models.
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
LONGTAIL=net-partition,log-injection,missing-config,bad-image

bash benchmark/preflight.sh --fix || { echo "PREFLIGHT FAILED"; exit 1; }
kubectl scale deploy --all -n boutique --replicas=0 >/dev/null 2>&1
# Exp 7 window 600 s: a 3-call 14B tool loop takes ~150 s per pod on this CPU, and the
# LLM-only loop may first diagnose a dependent pod; tau=300 would measure CPU speed.
# Outcomes are also reported at 300 s. (First attempt at 300 s set aside after 1 trial.)
run results/exp7a --max-wait 600 --testbed demo --conditions llm-generic-14b --scenarios $LONGTAIL --trials 5 --seed 20261008
run results/exp8a --testbed demo --conditions llm-only-llama8b,hybrid-llama8b --scenarios $LONGTAIL --trials 5 --seed 20261010
echo "DEMO DONE"

kubectl scale deploy --all -n agentic-demo --replicas=0 >/dev/null 2>&1
run results/exp7b --max-wait 600 --testbed boutique --poll 1 --conditions hybrid-generic-14b,llm-generic-14b \
    --scenarios missing-secret --trials 4 --seed 20261009
run results/exp8b --testbed boutique --poll 1 --conditions hybrid-llama8b \
    --scenarios missing-secret --trials 4 --seed 20261012
run results/exp9 --testbed boutique --poll 1 \
    --conditions baseline,rules,hybrid,hybrid-tools,hybrid-generic,hybrid-14b,hybrid-llama8b \
    --scenarios nonroot-release,probe-misconfig --trials 4 --seed 20261011
echo "ALL V5 LIVE DONE"

URL=http://172.18.0.6:11434
for m in qwen2.5:3b-instruct llama3.2:3b qwen2.5:7b-instruct llama3.1:8b gemma2:9b qwen2.5:14b-instruct; do
  python benchmark/prompt_sensitivity.py --url $URL --model "$m" --set traps
done
echo "ALL V5 DONE"
