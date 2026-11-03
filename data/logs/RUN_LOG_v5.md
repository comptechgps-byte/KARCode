# Run log — manuscript v5 experiments (2026-10-02/03)

Agent: v3.0, unchanged (`../freeze/FREEZE_v3.0.md`). Chain: `benchmark/run_v5_experiments.sh`,
log `results/v5_experiments.log`.

## Harness changes (before any v5 trial)
- New conditions in `run_benchmark.py`: `hybrid-generic-14b`, `llm-generic-14b`,
  `hybrid-llama8b`, `llm-only-llama8b` (environment only; `MAX_TOOL_STEPS=3`).
- `warm_model()`: before injection, the harness loads the condition's model with the
  agent's options (`num_ctx` 8192, `num_thread` 8). Needed because Exp 9 mixes three
  models in one randomized run; a model swap would otherwise add 10–20 s load time to
  the first LLM call. Measured: Llama load 10.6 s, resident 0.1 s. Earlier experiments
  ran one model per run, so their models stayed resident.
- Boutique catalog amendment (`testbeds/boutique/faults.yaml`): `nonroot-release`
  (paymentservice) and `probe-misconfig` (emailservice), held-out classes that make a
  pod unhealthy. Validated with the agent OFF (`results/validation_v5`, 1 trial each:
  neither recovers natively; signatures `CreateContainerConfigError` and Running/0-ready/
  0-restarts as intended) before any agent condition ran on them.
- Offline: `prompt_sensitivity.py --set traps` (three rollback-trap snapshots).

## Incidents
| When | What | Handling |
|---|---|---|
| first launch | preflight: DNS from `agentic-remediator-worker` failed once | re-checked 3/3 OK (no stale IPs; CoreDNS IPs in node CIDRs); one-off timeout; relaunched |
| Exp 7a, trial 1 | `llm-generic-14b` took 158 s to diagnose the *dependent* cache-proxy pod first; τ = 300 s ended before it reached the backend | Exp 7 would have measured CPU speed, not tool use. Chain stopped after 1 trial; that run set aside as `results/discarded_exp7a_tau300`; Exp 7 re-run with τ = 600 s, outcomes also reported at 300 s. Disclosed in the paper |

## Outcome
All live runs finished: exp7a (20), exp7b (8), exp8a (40), exp8b (4), exp9 (56) = 128 valid
trials; 0 suspended, 0 injection failures, 0 unsafe actions executed. Offline rollback-trap
study: 6 models x 3 traps x 3 prompts x 4 calls = 216 calls (`results/prompt_traps`).
Grand total over all experiments: 1,551 valid trials, 17 fault types (5 held out).