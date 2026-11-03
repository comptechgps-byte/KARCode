# K8s-Agentic-Remediator (KAR) — agent v3.1

A closed-loop, safety-gated remediation agent for Kubernetes that diagnoses
with a deterministic **rule engine first**, escalates to an **LLM only when no
rule yields an actionable remedy or a rule's action has failed** (v3.1), and passes every
proposed action through a **layered safety gate** (allow-list → scope → budget → risk tier)
after a **verify-before-act** check. The repository also contains the testbeds, fault
injectors and benchmark harness used in the accompanying article.

## Architecture

```
observe ──► rule engine ──(no / non-actionable match)──► LLM (Ollama or Anthropic)
  ▲             │ actionable match / defer                      │
  │             └──────────────► verify-before-act ◄────────────┘
  │                                     │
  │                               safety gate  (allow-list, scope, budget, tier)
  │                                     │
  └──── cool-down ◄──────────── act via Kubernetes API (generic actions)
```

| Module | Role |
|---|---|
| `agent/telemetry.py` | pod/container state **and last state**, events, log tail, change context (revision, age) |
| `agent/diagnosis.py` | modes `rules`, `hybrid`, `llm-only`, `restart` (naive baseline), `*-tools` (fault-specific read-only tools), `*-generic` (generic `k8s_get`/`k8s_describe`); rule table; LLM prompt + JSON schema; v3.1 failure-triggered escalation |
| `agent/tools.py` | read-only tools for the `-tools` and `-generic` modes (Secrets never readable) |
| `agent/llm.py` | provider-neutral client: **Ollama** (default, open-weight, reproducible) or Anthropic |
| `agent/safety.py` | layered gate: unknown action, target scope, per-Deployment action budget, risk tiers |
| `agent/remediation.py` | generic actions: revision rollback, bounded memory patch, revert recent NetworkPolicies, restart, scale |
| `agent/main.py` | control loop, deferral, verify-before-act, cool-down, `MTTR_EVENT` JSON audit log |

## Fault catalog (`chaos/chaos_scenarios.yaml`)

| Category | Scenarios |
|---|---|
| in-catalog (rule-covered) | `oom-kill`, `crashloop`, `net-partition` |
| out-of-catalog (LLM escalation) | `bad-image`, `missing-config` |
| self-healing (harm check) | `pod-kill`, `transient-crash` |
| adversarial (prompt injection in logs) | `log-injection` |

Online Boutique (`testbeds/boutique/faults.yaml`, frozen; v5 amendment dated):
transfer `bad-image`, `crash-release`, `memory-release`; held-out `missing-secret`,
`unschedulable`, `zero-replicas`, `nonroot-release`, `probe-misconfig`; self-healing `pod-kill`.

Conditions (`benchmark/run_benchmark.py`, `CONDITION_ENV`): `baseline`, `restart`, `rules`,
`hybrid`, `llm-only`, `hybrid-tools`, `llm-tools`, `hybrid-generic`, `llm-generic`, the
`-6` (six tool calls), `-14b` (Qwen2.5-14B), `-llama8b` (Llama-3.1-8B) and `-fe`
(failure-triggered escalation, agent v3.1) variants. Agent versions: v2.0 (Experiment 1), v2.1 (Experiments 2-3), v3.0
(Experiments 2R and 4-9) and v3.1 (Experiment 10).

## Reproducing the paper

Requirements: Docker (≥ 16 GB RAM assigned, ≥ 8 physical cores recommended). Everything
else runs in containers.

```bash
# 1. runner image (python, kind, kubectl, analysis libs; pinned versions)
docker build -t k8s-remediator-runner:1 tools/runner
docker run -d --name runner --network kind -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$PWD":/work -w /work k8s-remediator-runner:1 sleep infinity
#    (create the `kind` network first if it does not exist: docker network create kind)

# 2. cluster + Ollama + model + agent image + manifests
docker exec runner bash setup/setup_kind_cluster.sh

# 3. unit tests
docker exec runner python -m unittest discover -s tests -v

# 4. testbed preflight (always after a host or Docker restart)
docker exec runner bash benchmark/preflight.sh

# 5. experiments (randomized complete blocks; all resumable)
docker exec runner python benchmark/run_benchmark.py --trials 15 --out-dir results/main   # Exp 1
docker exec runner bash testbeds/boutique/setup.sh                                          # Online Boutique
docker exec runner bash benchmark/run_v2_experiments.sh                                     # Exp 2-3
docker exec runner bash benchmark/run_v3_experiments.sh                                     # Exp 4-5
docker exec runner bash benchmark/run_v4_experiments.sh                                     # Exp 6, 2R
docker exec runner bash benchmark/run_v5_experiments.sh                                     # Exp 7-9 (+ traps)
#    Exp 10 needs agent v3.1: rebuild the image (setup script, step 2), then
docker exec runner bash benchmark/run_v6_experiments.sh                                     # Exp 10 (+ traps2)

# 6. offline studies (Ollama IP: docker inspect ollama); models: qwen2.5:{3b,7b,14b}-instruct,
#    llama3.2:3b, llama3.1:8b, gemma2:9b
docker exec runner python benchmark/adversarial_eval.py --url http://<ollama-ip>:11434 --model qwen2.5:7b-instruct
docker exec runner python benchmark/prompt_sensitivity.py --url http://<ollama-ip>:11434 --model qwen2.5:7b-instruct [--set traps|traps2]

# 7. statistics, tables, figures, macros
docker exec runner python benchmark/analyze_results.py results/main --fig-dir paper/figures --tab-dir paper/tables
docker exec runner python benchmark/paper_numbers.py --main results/main --adv results/adversarial --paper paper
docker exec runner python benchmark/analyze_v2.py --exp1 results/main --exp2 results/exp2a results/exp2b \
  --exp3 results/exp3 --fig-dir paper/figures --tab-dir paper/tables --out results/v2_analysis
docker exec runner python benchmark/paper_numbers_v2.py
docker exec runner python benchmark/paper_numbers_v3.py
docker exec runner python benchmark/paper_numbers_v4.py
docker exec runner python benchmark/paper_numbers_v5.py
docker exec runner python benchmark/paper_numbers_v6.py   # after v5: both write tables/prompt_traps.tex
docker exec runner python benchmark/paper_numbers_v7.py
docker exec runner python benchmark/tables_v4.py          # consolidated outcome / TTR tables
```

Each `run_v*_experiments.sh` runs a preflight, the trials, the two pre-specified exclusion
checks (`suspend_check.py`, `injection_check.py`) and a re-run of excluded trials. The
experiments write trial-level results, decision logs and run metadata to `results/`; the
analysis scripts in step 7 read `results/` and write LaTeX tables, figures and macros to
`paper/`.

**Using the published data.** `data/results/` holds every run reported in the article
(see `data/README.md`). To regenerate the article's numbers without re-running the
experiments, link it as `results/` (`ln -s data/results results`) and run step 7 only.

The 14B model needs about 10 GB of RAM inside Docker; give the Docker/WSL2 VM
at least 24 GB (`memory=24GB` in `%UserProfile%\.wslconfig` on Windows).

On SMT hosts set `LLM_NUM_THREAD` (agent manifest) to the number of physical
cores: Ollama's default of one thread per logical CPU was ~18× slower here.

## Safety notes

- The agent holds **no ClusterRole**; its namespaced Role grants only the verbs its actions need.
- `ALLOW_DESTRUCTIVE=false` by default; node/storage actions are not executable in the prototype.
- `AUTO_APPROVE_MODERATE=true` is for unattended benchmark runs only; in production moderate
  actions (rollback, scaling, NetworkPolicy reversion) should go to a human approver.
- Research prototype: do not point it at a cluster you do not own.

## Repository layout

```
agent/        the agent (see table above)
benchmark/    harness (run_benchmark.py), experiment chains, offline studies, analysis scripts
chaos/        fault catalog and injector for the minimal testbed
testbeds/     Online Boutique testbed: manifest, frozen fault catalog, injector
manifests/    namespace, RBAC, demo app (+ services), agent, LLM endpoint template
docker/       agent image
setup/        Kind cluster, Ollama and agent setup script
tools/runner/ reproducible runner image (pinned versions)
tests/        unit tests for rules, escalation, gate and tools
data/         experimental data (results/) and the audit of the v1 prototype
```

## Authors

Department of Information Technology, National Institute of Technology Raipur, India

- Abhijeet Bajpayee (abajpayee.phd2026.it@nitrr.ac.in)
- Tirath Prasad Sahu (tpsahu.it@nitrr.ac.in)
- Rakesh Tripathi (rtripathi.it@nitrr.ac.in)

## License

MIT, see `LICENSE`. The Online Boutique manifests in `testbeds/boutique/`
(`upstream-kubernetes-manifests.yaml` and `boutique.yaml`, derived from it) come from
Google's microservices-demo project and remain under its Apache License 2.0.
