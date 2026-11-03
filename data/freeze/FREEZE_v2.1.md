# Agent freeze v2.1.1 (2026-09-30) — supersedes v2.1 below

**Why.** The first launch of Experiment 2a under v2.1 was stopped after 33
trials: the single-shot LLM path requested a 4,096-token context and the
tool-augmented path 8,192, and Ollama reloaded the model on every switch
between the two (confirmed in the Ollama log), adding reload time to the
latency of whichever call followed a switch. v2.1.1 uses one context size
(8,192) for every call. Nothing else changed. The 33 confounded trials are kept
in `results/discarded/exp2a_ctx_reload/` and are not reported. Experiments 2a,
2b and 3 were then run from scratch under v2.1.1.

Image: `k8s-agentic-remediator:v2.1.1` = `sha256:39137b89dd9bb94d67ad2c4eafbe45faf847a80ac270fdc8075d1ae881487cd3`
Changed file: `agent/llm.py` → `2e190682abc0dd3910bda367f320f195bbecfe687a17fbb2699514afbae66f5e`
(all other hashes as in the v2.1 table below).

Note: Experiment 1 (v2.0) used 4,096 throughout and never mixed context
sizes, so it is unaffected; the offline injection study likewise used a
single size.

---

# Agent freeze v2.1 (2026-09-30) — manuscript v2 experiments

Frozen before Experiments 2 (`results/exp2a`, `results/exp2b`) and 3
(`results/exp3`). No rule, prompt, tool, parameter or action implementation
was changed afterwards; each run's `run_meta_*.json` records these hashes.

Image: `k8s-agentic-remediator:v2.1` = `sha256:6a213190e0403d329341a0970986988cdbd9e902059b1292233fd603332d91f9`

| File | SHA-256 | vs v2.0 |
|---|---|---|
| agent/diagnosis.py | 6ae2e9a961d42efd116f04826c6027b4e9cdff73171fb66f5e6ac68772633b11 | new modes `hybrid-tools`, `llm-tools`; v2.0 modes unchanged |
| agent/llm.py | d72ea796728bc3441ad6a32bd99925f7975da0bc08418ea11087bdc8f339d388 | added `chat()` (multi-turn/tools); `complete()` unchanged |
| agent/main.py | 347acd6d1fa71bea4b922404239d28a88b611f38cbf7cf549aa97df4abfb6bec | records revisions observed healthy; builds tools |
| agent/remediation.py | d0f7973e31276941ca98615dbaa0113c02df774c033b52f535b1ecdfd52bdb71 | rollback targets last revision observed healthy |
| agent/safety.py | a91854d52b56a1e8d2cc1f797e6afa7cb9521414ac7305df6e34db111d2bdf6d | **unchanged** |
| agent/telemetry.py | a8a4c9f64a146e2e2d92882ef93c355b1ee678b1120d67078efd4b9d4fe6ca48 | **unchanged** |
| agent/tools.py | ea84669d46fdaa2f32fcd7542d149e73f16215e70a318262d04906663f054129 | new: 4 read-only tools |
| testbeds/boutique/faults.yaml | 6de1032b1172c13126b2fcf5036ba0ccf40f5ca7d648f50f6b8ab0628187ecd3 | new |
| testbeds/boutique/inject.sh | 1ec94e977e7a0b8d3d55a25d1c146bca9730e423be32f4317a4a168f387cff8b | new |

## Development record (all on the demo testbed only)
1. Tool-augmented modes smoke-tested on demo `missing-config` and
   `net-partition` (`results/smoke_v21`, not reported as results).
2. Tool output truncated to 2,000 characters after the smoke test showed
   89–146 s per tool-augmented diagnosis on CPU.
3. Observation window raised to 300 s for v2 experiments so that slow
   CPU-served tool loops are not censored merely for latency; results are
   reported at both 180 s (comparable with v1) and 300 s.

## Online Boutique faults
Frozen in `testbeds/boutique/faults.yaml` before any agent condition ran on
them; injectors validated with the agent **off** (`results/boutique_validation`).
`selector-break` was retired during that validation (fault stayed latent for
the whole window because of long-lived gRPC connections) and replaced by
`zero-replicas`, with the agent still never having run on either.
