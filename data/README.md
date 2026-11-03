# Experimental data

`results/` holds every run behind the article: trial-level results, the agent's decision
logs and run metadata, plus the offline studies. `audit-v1.md` documents the audit of the
earlier (withdrawn) v1 prototype that motivated the measurement pitfalls discussed in the
article.

To re-run the analysis on these data, make them visible where the scripts expect them:

```bash
ln -s data/results results        # Windows: mklink /D results data\results
```

## Runs reported in the article

| Folder | Experiment | Testbed | Agent | Conditions | Valid trials |
|---|---|---|---|---|---|
| `main/` | 1 | minimal (`Recreate`) | v2.0 | native, naive restart, rules, hybrid, LLM-only (7B) | 600 |
| `exp2a/` | 2 | minimal | v2.1.1 | hybrid, LLM-only, hybrid+tools, LLM+tools (7B) | 160 |
| `exp2b/` | 2 | minimal | v2.1.1 | hybrid, LLM-only (14B) | 80 |
| `exp2a_replication/` | 2R | minimal | v3.0 | the four 7B conditions of Experiment 2, same seed | 160 |
| `exp3/` | 3 | Online Boutique (upstream defaults) | v2.1.1 | native, naive restart, rules, hybrid, hybrid+tools, LLM+tools (7B) | 168 |
| `exp4a/`, `exp4b/` | 4 | minimal / Online Boutique | v3.0 | hybrid+generic, LLM+generic (7B) | 80 / 56 |
| `exp5a/`, `exp5b/` | 5 | minimal / Online Boutique | v3.0 | hybrid+tools (14B) / hybrid, hybrid+tools (14B) | 40 / 56 |
| `exp6a/`, `exp6b/` | 6 | minimal / Online Boutique | v3.0 | generic tools with six calls (7B) | 15 / 8 |
| `exp7a/`, `exp7b/` | 7 | minimal / Online Boutique | v3.0 | LLM+generic (14B); window 600 s | 20 / 8 |
| `exp8a/`, `exp8b/` | 8 | minimal / Online Boutique | v3.0 | LLM-only, hybrid (Llama-3.1-8B) | 40 / 4 |
| `exp9/` | 9 | Online Boutique | v3.0 | two added held-out faults, seven conditions | 56 |
| `exp10a/`, `exp10b/`, `exp10c/` | 10 | both | v3.1 | failure-triggered escalation (7B, 14B, Llama) and regression | 12 / 25 / 12 |

Offline studies (no cluster): `adversarial/` (prompt injection: 8 payloads, with and
without a "logs are untrusted" warning, 3 seeds, three models), `prompt_sensitivity/`
(three prompt wordings, six models of three families), `prompt_traps/` and
`prompt_traps2/` (rollback traps, six models). `v2_analysis/` holds intermediate
analysis tables.

Valid-trial counts exclude rows with `valid=False` (see below); `trials.csv` may hold a few
more rows than the table shows.

## Frozen agent images

Each run's `run_meta_*.json` records the agent image ID and the SHA-256 of every agent and
manifest file, so any deviation from the frozen version is detectable.

| Version | Image | Used in |
|---|---|---|
| v2.0 | `sha256:d9626a38cf530649dc6e66d57fefa0759708ad199f82aae13bd931c447480351` | Experiment 1 |
| v2.1.1 | `sha256:39137b89dd9bb94d67ad2c4eafbe45faf847a80ac270fdc8075d1ae881487cd3` | Experiments 2 and 3 |
| v3.0 | `sha256:b37d58d1e98beddd774b613f46ccaeaba92bb95877d27273e5fc2679c151efac` | Experiments 2R and 4-9 |
| v3.1 | `sha256:f8448998a72ef1636b3fc1599b779fe93dbf5e12600b9b30ea94b382c371630c` | Experiment 10 |

## Files in each run folder

- `trials.csv`: one row per trial. Key columns: `trial_id` (block-scenario-condition),
  `t0_utc` (fault injection), `t_fault_visible_s`, `ttr_s` (time to stable recovery,
  censored at the window), `recovered`, `user_down_s` (Online Boutique: seconds the
  scripted shopper failed), decision summaries (`first_action`, `n_executed`,
  `unsafe_proposals`, `unsafe_executed`, `collateral_actions`, `budget_rejections`,
  `n_llm_calls`, `llm_latency_s_total`), `mem_avail_mb`/`swap_used_mb` (from Experiment 2
  on), `valid` and `note`. Rows with `valid=False` were excluded by a pre-specified rule
  (host suspension, injection failure, testbed fault) and re-run; they are kept for
  transparency.
- `agent_events.jsonl`: the agent's structured decision record for every diagnosis:
  timestamps, diagnosis source, proposed action and target, gate check and outcome,
  action detail, LLM latency, tokens and tool calls. Re-runs reuse a `trial_id`;
  `benchmark/analyze_results.load_events` matches events to the valid run by time.
- `run_meta_*.json`: arguments, software versions, agent image ID, and SHA-256 of the
  frozen agent code and manifests at the start of each invocation. The recorded agent
  environment lists an optional `ANTHROPIC_API_KEY` reference to a Kubernetes Secret; no
  key was set, and all runs used Ollama.
- `*.log` (top level): console output of the experiment chains.

## Not reported (kept for transparency)

| Folder | Why it was set aside |
|---|---|
| `discarded/exp2a_ctx_reload/` | Experiment 2a first launch: Ollama reloaded the model between two context sizes, inflating LLM latency; agent v2.1.1 uses a single context size and the experiment was re-run. |
| `discarded/exp2b_memory_pressure/` | Experiment 2b first run: the Docker VM swapped with the 14B model resident and NetworkPolicy enforcement degraded; re-run in full with more VM memory. |
| `discarded/exp3_memory_pressure/` | Experiment 3 first launch stopped before its first trial completed (memory pressure). |
| `discarded/exp3_dns_fault/` | Experiment 3: 36 trials affected by intermittent cluster DNS failures after a host reboot (a CoreDNS pod kept a stale IP); Experiment 3 restarted from scratch, and the harness now checks for stale pod IPs before every trial. |
| `discarded_exp7a_tau300/` | Experiment 7, first trial: with a 300 s window the 14B tool loop was cut off by CPU speed; Experiment 7 was re-run with a 600 s window (outcomes at 300 s are also reported). |
| `discarded_v31_clearbug/` | Experiment 10a, first trial: agent build that cleared its failure record when any pod of the workload was Ready, so escalation never fired; fixed and re-frozen as v3.1 before the reported run. |
| `prompt_sensitivity/discarded/` | Gemma-2-9B calls made while the model was missing from Ollama; superseded by the valid re-run (see the note in that folder). |
| `pilot/`, `smoke*/`, `boutique_validation/`, `validation_v5/` | Development, smoke and injector-validation runs (the latter with the agent switched off). |
