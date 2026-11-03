# Agent freeze v3.1 (2026-10-03) — manuscript v6 experiments

Frozen before Experiment 10. One behavioural change, motivated by the rule misfire
observed in Experiment 9 (`probe-misconfig`): **failure-triggered escalation**.

Image: `k8s-agentic-remediator:v3.1` = `sha256:f8448998a72ef1636b3fc1599b779fe93dbf5e12600b9b30ea94b382c371630c`

| File | SHA-256 | vs v3.0 |
|---|---|---|
| agent/diagnosis.py | bb14f5cadaf39da655c2ae6988be2556fa6a8c8f7b0fa17246071eece4dd2589 | `diagnose(snap, rule_failure=None)`: in hybrid modes, an actionable rule whose action already failed on this workload no longer wins; the LLM is consulted with the rule finding **and** the failure detail as hint. `rules`, `llm-*`, `restart` modes and deferral unchanged |
| agent/main.py | 72a75023dfa377d7722fc9f48a6a3a90e68a0e31cbbbf2a8b8814f11d3e4b199 | records `rule_failures[workload] = (pod, detail)` when a rule action ends `action-failed`; cleared when *that pod* becomes Ready or no longer exists; `FAILURE_ESCALATION` env (default `1`; `0` = v3.0 behaviour); logged in the start line |
| agent/llm.py | 2e190682abc0dd3910bda367f320f195bbecfe687a17fbb2699514afbae66f5e | **unchanged** |
| agent/remediation.py | d0f7973e31276941ca98615dbaa0113c02df774c033b52f535b1ecdfd52bdb71 | **unchanged** |
| agent/safety.py | a91854d52b56a1e8d2cc1f797e6afa7cb9521414ac7305df6e34db111d2bdf6d | **unchanged** |
| agent/telemetry.py | a8a4c9f64a146e2e2d92882ef93c355b1ee678b1120d67078efd4b9d4fe6ca48 | **unchanged** |
| agent/tools.py | 3e12d666fbe410daf35d58497703106ca3381e3db25eba2b678d57d0950c90fd | **unchanged** |
| manifests/01-rbac.yaml | e13058c85f27bc7ec1a0095ea682327b3270cd248ae238b98e54e7b6e67eaa0a | **unchanged** |

Scope: only an `action-failed` outcome (the action could not change the cluster)
triggers escalation. A rule action that succeeds but does not cure the fault is
still bounded only by the action budget (not addressed in v3.1).

## Development record
Unit tests (`tests/test_agent.py`, 4 new tests, 31/31 pass). A first build
(`sha256:7968a4ed…`, main.py `226125c7…`) cleared the failure record whenever *any* pod of
the workload was Ready; under RollingUpdate the old pod stays Ready, so escalation never
fired. This was seen in the first Experiment 10 trial; the run was stopped and set aside
(`results/discarded_v31_clearbug`), the clearing condition fixed as above, and one smoke
trial (`results/smoke_v31`, not reported) confirmed that escalation fires. No prompt, rule
or tool was changed, and no outcome of the smoke trial influenced the design.

## Harness
Every pre-existing condition now sets `FAILURE_ESCALATION=0`, so re-running an earlier
condition on the v3.1 image reproduces v3.0. New `*-fe` conditions set it to `1`.
