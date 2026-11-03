"""
Developed and Edited by Abhijeet Bajpayee
main.py — the agent's closed control loop:

    observe   list pods, build snapshots (telemetry.py)
    diagnose  rules / LLM / hybrid / naive restart (diagnosis.py)
    verify-before-act
              re-read the pod after diagnosing; if it recovered or was
              replaced meanwhile (e.g. during a slow LLM call) the diagnosis
              is stale and the action is dropped
    approve   safety gate: action, scope, budget, risk tier (safety.py)
    act       generic Kubernetes-API remediation (remediation.py)
    cool down per-Deployment cool-down so the effect of an action is
              observed before the same workload is reconsidered

Each handled fault emits one `MTTR_EVENT {json}` line; the benchmark
harness collects these to decompose recovery time and audit decisions.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time

from kubernetes import client, config

from diagnosis import FAULT_SUSPECTED_TRANSIENT, DiagnosisEngine
from llm import build_llm_client
from remediation import RemediationExecutor, selector_matches
from tools import ClusterTools
from safety import SafetyGate
from telemetry import TelemetryCollector

logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("agent")

POLL_INTERVAL_S = float(os.environ.get("POLL_INTERVAL_S", "2"))
COOLDOWN_S = float(os.environ.get("COOLDOWN_S", "15"))
NAMESPACE = os.environ.get("WATCH_NAMESPACE", "agentic-demo")
DIAGNOSIS_MODE = os.environ.get("DIAGNOSIS_MODE", "hybrid")
FAILURE_ESCALATION = os.environ.get("FAILURE_ESCALATION", "1") == "1"   # v3.1
SELF_LABEL = ("app", "remediation-agent")


def load_kube_config():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


def emit(record: dict) -> None:
    print("MTTR_EVENT " + json.dumps(record, default=str), flush=True)


def main():
    load_kube_config()
    core, apps = client.CoreV1Api(), client.AppsV1Api()

    telemetry = TelemetryCollector(core, NAMESPACE, apps)
    uses_llm = DIAGNOSIS_MODE not in ("rules", "restart")
    llm_client = build_llm_client() if uses_llm else None
    networking = client.NetworkingV1Api()
    healthy_hashes: dict[str, set] = {}   # deployment -> pod-template-hashes observed Ready
    tools = None
    if DIAGNOSIS_MODE.endswith("-tools") or DIAGNOSIS_MODE.endswith("-generic"):
        tools = ClusterTools(core, apps, networking, NAMESPACE, healthy_hashes, selector_matches)
    engine = DiagnosisEngine(mode=DIAGNOSIS_MODE, llm_client=llm_client, tools=tools)
    gate = SafetyGate(namespace=NAMESPACE)
    remediator = RemediationExecutor(core, apps, NAMESPACE, networking, healthy_hashes)

    log.info("Agent started | namespace=%s mode=%s llm=%s model=%s allow_destructive=%s "
             "auto_approve_moderate=%s budget=%s/%ss failure_escalation=%s",
             NAMESPACE, DIAGNOSIS_MODE, type(llm_client).__name__ if llm_client else None,
             os.environ.get("LLM_MODEL"), gate.allow_destructive, gate.auto_approve_moderate,
             gate.action_budget, gate.budget_window_s, FAILURE_ESCALATION)

    cooldown_until: dict[str, float] = {}
    deferred: set[str] = set()
    # v3.1: workload -> detail of a rule action that failed on it; hybrid modes then
    # escalate instead of retrying. FAILURE_ESCALATION=0 reproduces v3.0 exactly.
    # Keyed by workload, valued (pod, detail): cleared only when *that* pod becomes Ready,
    # because under RollingUpdate the workload's old pod stays Ready throughout a fault.
    rule_failures: dict[str, tuple[str, str]] = {}

    while True:
        try:
            pods = telemetry.list_pods()
        except client.ApiException as e:
            log.error("Failed to list pods: %s", e.reason)
            time.sleep(POLL_INTERVAL_S)
            continue

        live = {p.metadata.name for p in pods}
        for wl in [w for w, (pname, _) in rule_failures.items() if pname not in live]:
            del rule_failures[wl]   # the failing pod is gone (replaced or rolled back)

        for pod in pods:
            if (pod.metadata.labels or {}).get(SELF_LABEL[0]) == SELF_LABEL[1]:
                continue  # never act on ourselves
            snap = telemetry.snapshot(pod)
            if snap.unhealthy_reason is None:
                if snap.owner_deployment and snap.containers and all(c.ready for c in snap.containers):
                    h = snap.labels.get("pod-template-hash")
                    if h:
                        healthy_hashes.setdefault(snap.owner_deployment, set()).add(h)
                    if rule_failures.get(snap.owner_deployment, ("",))[0] == snap.name:
                        del rule_failures[snap.owner_deployment]
                continue
            key = snap.owner_deployment or snap.name
            if time.time() < cooldown_until.get(key, 0):
                continue

            rec = {"mode": DIAGNOSIS_MODE, "pod": snap.name, "deployment": snap.owner_deployment,
                   "unhealthy_reason": snap.unhealthy_reason,
                   "t_detected": snap.first_seen_unhealthy_at, "t_diag_start": time.time()}
            failure = rule_failures.get(key)
            diag = engine.diagnose(snap, rule_failure=failure[1] if (failure and FAILURE_ESCALATION) else None)
            rec["t_diagnosed"] = time.time()

            if diag is None:
                rec.update(outcome="no-diagnosis")
                emit(rec)
                cooldown_until[key] = time.time() + COOLDOWN_S
                continue

            if diag.fault_class == FAULT_SUSPECTED_TRANSIENT:
                # rule-level deferral: re-check next poll, no cool-down, log once per pod
                if snap.name not in deferred:
                    deferred.add(snap.name)
                    rec.update(fault_class=diag.fault_class, diagnosis_source=diag.source,
                               proposed_action="noop", outcome="deferred")
                    emit(rec)
                continue

            rec.update(fault_class=diag.fault_class, diagnosis_source=diag.source,
                       confidence=diag.confidence, proposed_action=diag.proposed_action,
                       action_target=diag.action_target, rule_hint=diag.rule_hint,
                       reasoning=diag.reasoning[:300], llm=diag.llm)

            # verify-before-act: drop stale diagnoses
            fresh = telemetry.read_pod(snap.name)
            if fresh is None or telemetry.snapshot(fresh, with_context=False).unhealthy_reason is None:
                rec.update(outcome="stale-aborted", t_acted=time.time())
                emit(rec)
                continue

            approval = gate.evaluate(diag.proposed_action, diag.action_target, diag.reasoning,
                                     pod=snap.name, deployment=snap.owner_deployment)
            rec.update(risk=approval.risk, approved=approval.approved,
                       approval_reason=approval.reason, gate_check=approval.check,
                       t_approved=time.time())

            executed, detail = False, ""
            if approval.approved and diag.proposed_action != "noop":
                result = remediator.execute(diag.proposed_action, snap)
                executed, detail = result.ok, result.detail
                if result.ok:
                    gate.record_execution(snap.name, snap.owner_deployment)
            rec.update(executed=executed, action_detail=detail, t_acted=time.time(),
                       outcome=("executed" if executed else
                                "rejected" if not approval.approved else
                                "noop" if diag.proposed_action == "noop" else "action-failed"))
            if rec["outcome"] == "action-failed" and diag.source == "rules":
                rule_failures[key] = (snap.name, (detail or "action had no effect")[:200])
            log.info("%s %s -> %s (%s) %s", snap.name, diag.fault_class, diag.proposed_action,
                     rec["outcome"], detail or approval.reason)
            emit(rec)
            cooldown_until[key] = time.time() + COOLDOWN_S

        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
