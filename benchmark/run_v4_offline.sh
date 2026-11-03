#!/usr/bin/env bash
# v4 offline studies. Run only after the live chain has finished
# (they share the CPU and the Ollama server with the agent):
#   - prompt/family sensitivity for four more models (R1)
#   - offline prompt-injection study for the 14B model
set -uo pipefail
cd "$(dirname "$0")/.."
URL=http://172.18.0.6:11434
until grep -q "ALL V4 EXPERIMENTS DONE" results/v4_experiments.log; do sleep 60; done
for m in qwen2.5:3b-instruct llama3.2:3b llama3.1:8b gemma2:9b; do
  python benchmark/prompt_sensitivity.py --url $URL --model "$m"
done
python benchmark/adversarial_eval.py --url $URL --model qwen2.5:14b-instruct
echo "ALL V4 OFFLINE DONE"
