# Agent freeze v2.0 (2026-09-29)

The agent was developed against a pilot (`results/pilot`, `results/smoke`; not
reported as results). Before the confirmatory run (`results/main`) the agent
was frozen; no rule, prompt, parameter or action implementation was changed
afterwards. Each run's `run_meta_*.json` records the SHA-256 of every file
below and the agent image ID, so any deviation is detectable.

Image: `k8s-agentic-remediator:v2.0` = `sha256:d9626a38cf530649dc6e66d57fefa0759708ad199f82aae13bd931c447480351`

| File | SHA-256 |
|---|---|
| agent/diagnosis.py | 24b80d83b6b75bcdab12542415d14fde8819750d655ae8543ccb29b259ec420f |
| agent/llm.py | 7dfb828a718af11beffd20fcd8bee74fa50e7e35b3b0614f0a95b6de336fffda |
| agent/main.py | f9fd293aa862b8742bab2f1a77211e9d34c6b98febca2f1eae5ace23e84c6a1d |
| agent/remediation.py | e06689219e44085f74e5ddfa177328d2867a366cab6e7dc399679ab62034a723 |
| agent/safety.py | a91854d52b56a1e8d2cc1f797e6afa7cb9521414ac7305df6e34db111d2bdf6d |
| agent/telemetry.py | a8a4c9f64a146e2e2d92882ef93c355b1ee678b1120d67078efd4b9d4fe6ca48 |

## Changes made during development (pilot-driven), all before the freeze

1. Verify-before-act re-check fetched no events, so dependency faults could
   never be re-confirmed (every net-partition action aborted as stale). Fixed:
   events are always read.
2. Early crashes (< 2 restarts) escalated to the LLM, which proposed premature
   restarts. Fixed: rule-level *defer* decision (no action, no escalation).
3. Added change context (revision, age, previous revision available) to the
   snapshot, as an SRE would check "what changed?" first.
4. Ollama pinned to 8 threads (default was ~18x slower on this SMT host).

Observed but deliberately **not** tuned away (reported as findings):
the LLM choosing `patch-limits`/`restart-pod` for a missing ConfigMap it had
correctly identified, and being hijacked by the log-injection payload.
