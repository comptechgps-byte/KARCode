# Run log — manuscript v6 experiments (2026-10-03)

Agent: v3.1 (`../freeze/FREEZE_v3.1.md`, failure-triggered escalation). Chains:
`benchmark/run_v6_experiments.sh` (log `results/v6_experiments.log`) and, after the
kindnet incident, `benchmark/run_v6_resume.sh` (log `results/v6_resume.log`).

| When | What | Handling |
|---|---|---|
| Exp 10a, first trial (build `7968a4ed…`) | failure record cleared whenever any pod of the workload was Ready; under RollingUpdate the old emailservice pod stays Ready, so escalation never fired | chain stopped; run set aside (`results/discarded_v31_clearbug`); `main.py` fixed to clear only when *the failing pod* becomes Ready or disappears; unit tests 31/31; one smoke trial (`results/smoke_v31`, not reported) showed escalation firing; re-frozen as `f8448998…` |
| Exp 10b, first pass | kindnet lost NetworkPolicy enforcement: all five `net-partition` trials invalid (fault never visible); the harness' re-run pass would have hit the same state | chain stopped; `preflight.sh --fix` (0/3 → restart → 3/3); `run_v6_resume.sh` re-ran exactly the invalid trials (all five valid, 10–12 s, rule path) |

## Outcome
exp10a (12), exp10b (25), exp10c (12) = 49 valid trials; 0 suspended, 0 injection
failures after the re-run, 0 unsafe actions executed. Regression (exp10b + exp10c,
37 trials): 37/37 repaired, 0 LLM calls, 0 escalations. Offline trap extension
(`results/prompt_traps2`): 6 models × 4 traps × 3 prompts × 4 calls = 288 calls.
Grand total over all experiments: 1,600 valid trials.
