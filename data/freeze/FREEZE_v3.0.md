# Agent freeze v3.0 (2026-10-02) — manuscript v3 experiments

Frozen before Experiments 4 (generic tools) and 5 (14B completion). Each
run's `run_meta_*.json` records these hashes.

Image: `k8s-agentic-remediator:v3.0` = `sha256:b37d58d1e98beddd774b613f46ccaeaba92bb95877d27273e5fc2679c151efac`

| File | SHA-256 | vs v2.1.1 |
|---|---|---|
| agent/diagnosis.py | 4d2251955d77105a7942d3718eca026562467c483f2726076522c38f955b35ce | modes `hybrid-generic`, `llm-generic`; tool log records inspected kind; all earlier modes unchanged |
| agent/tools.py | 3e12d666fbe410daf35d58497703106ca3381e3db25eba2b678d57d0950c90fd | generic `k8s_get` / `k8s_describe` over 17 standard namespaced kinds (no Secrets) |
| agent/main.py | b5e9e81b0ff29effa5f23e48366d523de3967a710b15183e12d9cc97f2fd68ed | builds tools for `-generic` modes |
| agent/llm.py | 2e190682abc0dd3910bda367f320f195bbecfe687a17fbb2699514afbae66f5e | **unchanged** |
| agent/remediation.py | d0f7973e31276941ca98615dbaa0113c02df774c033b52f535b1ecdfd52bdb71 | **unchanged** |
| agent/safety.py | a91854d52b56a1e8d2cc1f797e6afa7cb9521414ac7305df6e34db111d2bdf6d | **unchanged** |
| agent/telemetry.py | a8a4c9f64a146e2e2d92882ef93c355b1ee678b1120d67078efd4b9d4fe6ca48 | **unchanged** |
| manifests/01-rbac.yaml | e13058c85f27bc7ec1a0095ea682327b3270cd248ae238b98e54e7b6e67eaa0a | read-only `list` for the generic kinds; Secrets still not readable (verified with `kubectl auth can-i`) |

## Development record
Generic modes smoke-tested on the minimal testbed only (`results/smoke_v3`,
not reported): `missing-config` repaired by both generic modes;
`net-partition` not repaired by LLM+generic. After the smoke test the tool log
was extended to record the inspected kind (logging only). No prompt, rule or
tool change was made in response to outcomes.
