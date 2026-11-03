"""
Developed and Edited by Abhijeet Bajpayee
remediation.py — executes approved actions against the Kubernetes API.

Every action is generic (no knowledge of how the benchmark injects faults)
and narrowly scoped to the diagnosed pod or its owning Deployment. Each
returns an ActionResult whose `ok` is True only if the cluster was actually
changed as intended — v1 reported `executed=true` for no-ops and for silent
fallbacks to a different action.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from kubernetes import client

from telemetry import PodSnapshot

log = logging.getLogger("remediation")

REVISION_ANN = "deployment.kubernetes.io/revision"
MAX_MEMORY = os.environ.get("MAX_MEMORY", "1Gi")   # patch-limits never exceeds this


@dataclass
class ActionResult:
    ok: bool
    detail: str


class RemediationExecutor:
    def __init__(self, core_api: client.CoreV1Api, apps_api: client.AppsV1Api, namespace: str,
                 networking_api: Optional[client.NetworkingV1Api] = None,
                 healthy_hashes: Optional[dict] = None):
        self.core = core_api
        self.apps = apps_api
        self.namespace = namespace
        self.networking = networking_api or client.NetworkingV1Api()
        # deployment -> pod-template-hashes the agent has observed fully Ready (v2.1)
        self.healthy_hashes = healthy_hashes if healthy_hashes is not None else {}

    def execute(self, action: str, snap: PodSnapshot) -> ActionResult:
        handler = {
            "restart-pod": self.restart_pod,
            "patch-limits": self.patch_memory_limits,
            "rollback-deployment": self.rollback_deployment,
            "scale-deployment": self.scale_deployment,
            "revert-network-policy": self.revert_network_policy,
        }.get(action)
        if handler is None:
            return ActionResult(False, f"no executor for action {action!r}")
        try:
            return handler(snap)
        except client.ApiException as e:
            log.error("%s failed for %s: %s %s", action, snap.name, e.status, e.reason)
            return ActionResult(False, f"API error {e.status} {e.reason}")

    # ------------------------------------------------------------------ #
    def restart_pod(self, snap: PodSnapshot) -> ActionResult:
        self.core.delete_namespaced_pod(snap.name, self.namespace, grace_period_seconds=0)
        return ActionResult(True, f"deleted pod {snap.name}")

    def patch_memory_limits(self, snap: PodSnapshot) -> ActionResult:
        """Double memory request/limit of the owning Deployment's containers,
        capped at MAX_MEMORY. The Deployment's rollout replaces the pod."""
        dep_name = snap.owner_deployment
        if not dep_name:
            return ActionResult(False, "pod has no owning Deployment")
        dep = self.apps.read_namespaced_deployment(dep_name, self.namespace)
        cap = parse_quantity(MAX_MEMORY)
        patches, changes = [], []
        for c in dep.spec.template.spec.containers:
            res = c.resources or client.V1ResourceRequirements()
            limits, requests = dict(res.limits or {}), dict(res.requests or {})
            if "memory" not in limits:
                continue
            old = parse_quantity(limits["memory"])
            new = min(old * 2, cap)
            if new <= old:
                continue
            limits["memory"] = format_mib(new)
            if "memory" in requests:
                requests["memory"] = format_mib(min(parse_quantity(requests["memory"]) * 2, new))
            patches.append({"name": c.name, "resources": {"limits": limits, "requests": requests}})
            changes.append(f"{c.name}: {format_mib(old)}->{format_mib(new)}")
        if not patches:
            return ActionResult(False, f"no memory limit to raise (cap {MAX_MEMORY})")
        self.apps.patch_namespaced_deployment(
            dep_name, self.namespace, {"spec": {"template": {"spec": {"containers": patches}}}})
        return ActionResult(True, f"patched {dep_name} memory " + ", ".join(changes))

    def rollback_deployment(self, snap: PodSnapshot) -> ActionResult:
        """Restore the pod template of the newest older revision the agent
        has *observed healthy* (v2.1); if none was observed, the newest older
        revision, as `kubectl rollout undo` does (v2.0 behaviour). Targeting
        the last healthy revision prevents the rollback from restoring a
        broken intermediate revision created by an earlier wrong action."""
        dep_name = snap.owner_deployment
        if not dep_name:
            return ActionResult(False, "pod has no owning Deployment")
        dep = self.apps.read_namespaced_deployment(dep_name, self.namespace)
        current = int((dep.metadata.annotations or {}).get(REVISION_ANN, "0"))
        owned = [rs for rs in self.apps.list_namespaced_replica_set(self.namespace).items
                 if any(o.kind == "Deployment" and o.uid == dep.metadata.uid
                        for o in rs.metadata.owner_references or [])]
        older = [(int((rs.metadata.annotations or {}).get(REVISION_ANN, "0")), rs) for rs in owned]
        older = [(rev, rs) for rev, rs in older if 0 < rev < current]
        if not older:
            return ActionResult(False, f"{dep_name} has no revision older than {current}")
        healthy = self.healthy_hashes.get(dep_name, set())
        known_good = [(r, rs) for r, rs in older
                      if (rs.spec.template.metadata.labels or {}).get("pod-template-hash") in healthy]
        rev, rs = max(known_good or older, key=lambda t: t[0])
        basis = "last observed healthy" if known_good else "previous"
        template = client.ApiClient().sanitize_for_serialization(rs.spec.template)
        template.get("metadata", {}).get("labels", {}).pop("pod-template-hash", None)
        # JSON-patch replace (not strategic merge) so fields added by the bad
        # release, e.g. an extra env var, are removed rather than merged.
        self.apps.patch_namespaced_deployment(
            dep_name, self.namespace, [{"op": "replace", "path": "/spec/template", "value": template}])
        return ActionResult(True, f"rolled back {dep_name} from revision {current} to {rev} ({basis})")

    def scale_deployment(self, snap: PodSnapshot) -> ActionResult:
        dep_name = snap.owner_deployment
        if not dep_name:
            return ActionResult(False, "pod has no owning Deployment")
        scale = self.apps.read_namespaced_deployment_scale(dep_name, self.namespace)
        replicas = (scale.spec.replicas or 0) + 1
        self.apps.patch_namespaced_deployment_scale(dep_name, self.namespace,
                                                    {"spec": {"replicas": replicas}})
        return ActionResult(True, f"scaled {dep_name} to {replicas}")

    def revert_network_policy(self, snap: PodSnapshot) -> ActionResult:
        """Delete NetworkPolicies that (a) select this pod and (b) were
        created after the pod started, i.e. policy changes introduced while
        the workload was already running. Pre-existing policies are never
        touched. (v1 deleted a hard-coded policy name.)"""
        if snap.start_time is None:
            return ActionResult(False, "pod start time unknown")
        started = datetime.fromtimestamp(snap.start_time, tz=timezone.utc)
        removed = []
        for np in self.networking.list_namespaced_network_policy(self.namespace).items:
            if np.metadata.creation_timestamp <= started:
                continue
            if not selector_matches(np.spec.pod_selector, snap.labels):
                continue
            self.networking.delete_namespaced_network_policy(np.metadata.name, self.namespace)
            removed.append(np.metadata.name)
        if not removed:
            return ActionResult(False, "no NetworkPolicy introduced after pod start selects this pod")
        return ActionResult(True, "removed NetworkPolicy " + ",".join(removed))


# ---------------------------------------------------------------------- #
def selector_matches(selector: Optional[client.V1LabelSelector], labels: dict) -> bool:
    if selector is None:
        return True
    for k, v in (selector.match_labels or {}).items():
        if labels.get(k) != v:
            return False
    for expr in selector.match_expressions or []:
        has, val = expr.key in labels, labels.get(expr.key)
        if expr.operator == "In" and val not in (expr.values or []):
            return False
        if expr.operator == "NotIn" and has and val in (expr.values or []):
            return False
        if expr.operator == "Exists" and not has:
            return False
        if expr.operator == "DoesNotExist" and has:
            return False
    return True


_UNITS = {"": 1, "k": 10**3, "M": 10**6, "G": 10**9, "T": 10**12,
          "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40}


def parse_quantity(q: str) -> int:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([kMGT]i?|)", str(q).strip())
    if not m:
        raise ValueError(f"unsupported memory quantity {q!r}")
    return int(float(m.group(1)) * _UNITS[m.group(2)])


def format_mib(n_bytes: int) -> str:
    return f"{max(1, round(n_bytes / 2**20))}Mi"
