#!/usr/bin/env bash
# Manuscript v2 experiments (agent frozen at v2.1).
# Resumable: re-running skips completed trials. Log: results/v2_experiments.log
set -uo pipefail
cd "$(dirname "$0")/.."
B="python benchmark/run_benchmark.py --max-wait 300"

# Exp 2a — demo testbed, 7B: tool-augmented modes vs. v2.1 anchors
kubectl scale deploy --all -n boutique --replicas=0 >/dev/null 2>&1
$B --testbed demo --conditions hybrid,llm-only,hybrid-tools,llm-tools \
   --trials 5 --seed 20260930 --out-dir results/exp2a
python benchmark/suspend_check.py results/exp2a --apply
$B --testbed demo --conditions hybrid,llm-only,hybrid-tools,llm-tools \
   --trials 5 --seed 20260930 --out-dir results/exp2a

# Exp 2b — demo testbed, 14B (one model resident at a time)
$B --testbed demo --conditions hybrid-14b,llm-only-14b \
   --trials 5 --seed 20260931 --out-dir results/exp2b
python benchmark/suspend_check.py results/exp2b --apply
$B --testbed demo --conditions hybrid-14b,llm-only-14b \
   --trials 5 --seed 20260931 --out-dir results/exp2b

# Exp 3 — Online Boutique (upstream defaults), held-out + transfer faults
$B --testbed boutique --poll 1 \
   --conditions baseline,restart,rules,hybrid,hybrid-tools,llm-tools \
   --trials 4 --seed 20261001 --out-dir results/exp3
python benchmark/suspend_check.py results/exp3 --apply --slack 180
$B --testbed boutique --poll 1 \
   --conditions baseline,restart,rules,hybrid,hybrid-tools,llm-tools \
   --trials 4 --seed 20261001 --out-dir results/exp3
echo "ALL V2 EXPERIMENTS DONE"
