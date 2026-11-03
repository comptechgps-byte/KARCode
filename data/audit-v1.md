# Audit of the v1 testbed and results (2026-09-29)

The v1 code is not part of this repository. Every finding below was
reproduced on a fresh Kind cluster (kind v0.29.0, Kubernetes v1.33.1) by
deploying the v1 manifests and agent image unmodified. Consequence: **the v1
results (`results_combined_n30.csv`, pooled 85.6% vs 44.4% recovery, 48.2 s vs
114.9 s MTTR) are withdrawn** and must not be cited; they are neither
reproducible from the shipped code nor valid measurements of the claimed
mechanism.

| # | Defect | Evidence (reproduced) | Effect on v1 results | Fix in v2 |
|---|--------|-----------------------|----------------------|-----------|
| 1 | Results not traceable to code | `results_combined_n30.csv` absent; manuscript describes harness retries and incremental CSV writes that `run_benchmark.py` does not implement; OOM injection redesigned after the data were collected | Numbers unverifiable | Harness writes per-trial CSV + agent decision JSONL + run metadata; paper numbers generated only by `analyze_results.py` from shipped data |
| 2 | Fault injection did not cause an outage | Default `RollingUpdate` with `replicas: 1` (maxUnavailable rounds to 0): after `kubectl set env CRASH=1` the old pod stayed `1/1 Running` for >60 s while the new pod crash-looped | "Baseline recoveries" of structurally unrecoverable faults (40/90) were measurement artefacts: `is_pod_healthy` read `items[0]`, sometimes the untouched old pod | `strategy: Recreate`; Deployment-level health (observedGeneration, updated = ready = replicas, no stale pods) |
| 3 | Net-partition target never healthy | `backend` ran `sleep infinity` and listened on no port; `cache-proxy` readiness probe (`wget backend:8080`) failed with *Connection refused* with **no fault injected**; `kubectl rollout status` of cache-proxy never completes | Scenario measured nothing meaningful | backend serves HTTP on :8080 with its own readiness probe |
| 4 | Agent acted on a healthy cluster | Because of #3 the agent "remediated" cache-proxy every 15 s (4 `remove-network-policy` actions/min) against a non-existent policy, logged `executed: true` | Inflated/meaningless action logs | #3 fixed; actions report `ok` only if the cluster changed |
| 5 | OOM remediation never ran | Role lacked `replicasets get`; `_find_owning_deployment` raised, `patch-limits` silently fell back to deleting the pod and reported success. Observed: 40 pod deletions in 90 s, memory limit still 64Mi | The mechanism the paper describes (limit doubling) was never exercised; agent caused a restart storm | Owner resolved from `pod-template-hash` naming; RBAC grants RS read; no silent fallbacks; per-Deployment action budget |
| 6 | OOM reason lost after restart | Rules read only `state.terminated`; after the first restart `OOMKilled` is in `lastState` | Misclassification as crash loop | Rules read `state` and `lastState`, OOM checked first |
| 7 | Remediations were oracles | `rollback` hard-coded `CRASH=0`; `remove-network-policy` deleted the policy by its chaos name | Remediation success reflected knowledge of the injector | Real revision rollback (`rollout undo` semantics); revert only NetworkPolicies created after pod start that select the pod |
| 8 | Recovery could be a flap | No stability window; a pod Ready for one poll before crashing again counted as recovered | Optimistic MTTR | Recovery = start of a ≥10 s continuously healthy interval |
| 9 | LLM path never exercised | Every catalog fault matched a rule; no API key in runs | Title/abstract claim ("LLM-escalated") untested | Out-of-catalog + adversarial scenarios, `hybrid` vs `llm-only` ablation with a local open-weight model |
| 10 | Strawman baseline | All faults chosen so native healing cannot fix them | Agent superiority true by construction | Naive-restart and rules-only baselines; self-healing scenarios test for harm |

## Lessons (methodological contribution)

Three of these defects (#2, #5, #8) are generic pitfalls for any MTTR benchmark
of Kubernetes self-healing and are discussed in the paper as threats that
published evaluations should explicitly rule out:
rollout-masked faults, silently substituted remediations, and flapping
recoveries.
