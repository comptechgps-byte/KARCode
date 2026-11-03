"""
tools.py — read-only cluster tools for the tool-augmented LLM modes
(`hybrid-tools`, `llm-tools`). They let the model look beyond the pod
snapshot the way an SRE would ("what changed?", "is the referenced config
there?", "which policies select this pod?"), without granting any write
access. Tool output is still workload-influenced data (events, env values),
so it is returned as data and never interpreted as instructions.

All tools are namespace-scoped and use only verbs already granted by the
agent's Role (+ `configmaps get/list` for existence checks). Secrets are
never read: their existence is reported as "not checked".
"""
from __future__ import annotations

import json
import time
from typing import Callable, Optional

from kubernetes import client

REVISION_ANN = "deployment.kubernetes.io/revision"

TOOL_SPECS = [
    {"type": "function", "function": {
        "name": "get_rollout_history",
        "description": "Revisions of a Deployment (newest first): age, container images, env var "
                       "names, referenced ConfigMaps/Secrets, and whether the agent has observed "
                       "that revision healthy.",
        "parameters": {"type": "object", "properties": {
            "deployment": {"type": "string"}}, "required": ["deployment"]}}},
    {"type": "function", "function": {
        "name": "get_network_policies",
        "description": "NetworkPolicies in the namespace, whether each selects the given pod, "
                       "its policy types and rule counts, and whether it was created after the "
                       "pod started.",
        "parameters": {"type": "object", "properties": {
            "pod": {"type": "string"}}, "required": ["pod"]}}},
    {"type": "function", "function": {
        "name": "check_config_references",
        "description": "ConfigMaps and Secrets referenced by a Deployment's current pod template "
                       "and whether each ConfigMap exists.",
        "parameters": {"type": "object", "properties": {
            "deployment": {"type": "string"}}, "required": ["deployment"]}}},
    {"type": "function", "function": {
        "name": "get_recent_warning_events",
        "description": "Warning events in the namespace from the last 10 minutes (most recent first).",
        "parameters": {"type": "object", "properties": {}}}},
]


# --- v3.0: generic read-only tools ---------------------------------------
# Unlike TOOL_SPECS above, these name no fault class: the model must decide
# which object kinds to inspect. Secrets are never readable.
GENERIC_KINDS = ["pods", "deployments", "replicasets", "statefulsets", "daemonsets", "jobs",
                 "services", "endpoints", "ingresses", "configmaps", "persistentvolumeclaims",
                 "networkpolicies", "horizontalpodautoscalers", "resourcequotas", "limitranges",
                 "serviceaccounts", "events"]
GENERIC_TOOL_SPECS = [
    {"type": "function", "function": {
        "name": "k8s_get",
        "description": "List objects of one kind in the namespace with a one-line summary each "
                       "(like `kubectl get`). Kinds: " + ", ".join(GENERIC_KINDS) + ".",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": GENERIC_KINDS}}, "required": ["kind"]}}},
    {"type": "function", "function": {
        "name": "k8s_describe",
        "description": "Full spec and status of one object (like `kubectl describe`). "
                       "Kinds: " + ", ".join(k for k in GENERIC_KINDS if k != "events") + ".",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": [k for k in GENERIC_KINDS if k != "events"]},
            "name": {"type": "string"}}, "required": ["kind", "name"]}}},
]


def _age(ts) -> Optional[int]:
    return None if ts is None else int(time.time() - ts.timestamp())


def _refs(pod_spec) -> tuple[set, set]:
    cms, secrets = set(), set()
    for c in (pod_spec.containers or []) + (pod_spec.init_containers or []):
        for e in c.env or []:
            vf = e.value_from
            if vf and vf.config_map_key_ref:
                cms.add(vf.config_map_key_ref.name)
            if vf and vf.secret_key_ref:
                secrets.add(vf.secret_key_ref.name)
        for ef in c.env_from or []:
            if ef.config_map_ref:
                cms.add(ef.config_map_ref.name)
            if ef.secret_ref:
                secrets.add(ef.secret_ref.name)
    for v in pod_spec.volumes or []:
        if v.config_map:
            cms.add(v.config_map.name)
        if v.secret:
            secrets.add(v.secret.secret_name)
    return cms, secrets


class ClusterTools:
    def __init__(self, core: client.CoreV1Api, apps: client.AppsV1Api,
                 networking: client.NetworkingV1Api, namespace: str,
                 healthy_hashes: Optional[dict] = None,
                 selector_matches: Optional[Callable] = None):
        self.core, self.apps, self.net = core, apps, networking
        self.namespace = namespace
        self.healthy_hashes = healthy_hashes if healthy_hashes is not None else {}
        self.selector_matches = selector_matches

    def call(self, name: str, args: dict) -> str:
        fn = {"get_rollout_history": self.get_rollout_history,
              "get_network_policies": self.get_network_policies,
              "check_config_references": self.check_config_references,
              "get_recent_warning_events": self.get_recent_warning_events,
              "k8s_get": self.k8s_get,
              "k8s_describe": self.k8s_describe}.get(name)
        if fn is None:
            return json.dumps({"error": f"unknown tool {name!r}"})
        try:
            return json.dumps(fn(**(args or {})), default=str)[:2000]
        except TypeError as e:
            return json.dumps({"error": f"bad arguments: {e}"})
        except client.ApiException as e:
            return json.dumps({"error": f"API {e.status} {e.reason}"})

    # ------------------------------------------------------------------ #
    def get_rollout_history(self, deployment: str) -> dict:
        dep = self.apps.read_namespaced_deployment(deployment, self.namespace)
        current = int((dep.metadata.annotations or {}).get(REVISION_ANN, "0"))
        out = []
        for rs in self.apps.list_namespaced_replica_set(self.namespace).items:
            if not any(o.kind == "Deployment" and o.uid == dep.metadata.uid
                       for o in rs.metadata.owner_references or []):
                continue
            spec = rs.spec.template.spec
            cms, secrets = _refs(spec)
            h = (rs.spec.template.metadata.labels or {}).get("pod-template-hash")
            out.append({
                "revision": int((rs.metadata.annotations or {}).get(REVISION_ANN, "0")),
                "current": int((rs.metadata.annotations or {}).get(REVISION_ANN, "0")) == current,
                "age_s": _age(rs.metadata.creation_timestamp),
                "images": [c.image for c in spec.containers],
                "env": {e.name: (e.value[:40] if e.value is not None else "<from ref>")
                        for c in spec.containers for e in (c.env or [])},
                "memory_limits": [((c.resources.limits or {}).get("memory") if c.resources else None)
                                  for c in spec.containers],
                "configmap_refs": sorted(cms), "secret_refs": sorted(secrets),
                "observed_healthy": h in self.healthy_hashes.get(deployment, set()),
            })
        out.sort(key=lambda r: -r["revision"])
        return {"deployment": deployment, "current_revision": current, "revisions": out[:6]}

    def get_network_policies(self, pod: str) -> dict:
        p = self.core.read_namespaced_pod(pod, self.namespace)
        labels = p.metadata.labels or {}
        started = p.status.start_time
        res = []
        for np in self.net.list_namespaced_network_policy(self.namespace).items:
            sel = self.selector_matches(np.spec.pod_selector, labels) if self.selector_matches else None
            res.append({
                "name": np.metadata.name,
                "selects_pod": sel,
                "policy_types": np.spec.policy_types,
                "ingress_rules": len(np.spec.ingress or []),
                "egress_rules": len(np.spec.egress or []),
                "age_s": _age(np.metadata.creation_timestamp),
                "created_after_pod_start": bool(started and np.metadata.creation_timestamp > started),
            })
        return {"pod": pod, "network_policies": res}

    def check_config_references(self, deployment: str) -> dict:
        dep = self.apps.read_namespaced_deployment(deployment, self.namespace)
        cms, secrets = _refs(dep.spec.template.spec)
        existing = {c.metadata.name for c in self.core.list_namespaced_config_map(self.namespace).items}
        return {"deployment": deployment,
                "configmaps": {c: ("exists" if c in existing else "MISSING") for c in sorted(cms)},
                "secrets": {s: "not checked (no secret access)" for s in sorted(secrets)}}

    # ---------------- generic tools (v3.0) ---------------------------------
    def _list(self, kind: str):
        ns = self.namespace
        batch, autoscaling = client.BatchV1Api(), client.AutoscalingV2Api()
        return {
            "pods": lambda: self.core.list_namespaced_pod(ns).items,
            "deployments": lambda: self.apps.list_namespaced_deployment(ns).items,
            "replicasets": lambda: self.apps.list_namespaced_replica_set(ns).items,
            "statefulsets": lambda: self.apps.list_namespaced_stateful_set(ns).items,
            "daemonsets": lambda: self.apps.list_namespaced_daemon_set(ns).items,
            "jobs": lambda: batch.list_namespaced_job(ns).items,
            "services": lambda: self.core.list_namespaced_service(ns).items,
            "endpoints": lambda: self.core.list_namespaced_endpoints(ns).items,
            "ingresses": lambda: self.net.list_namespaced_ingress(ns).items,
            "configmaps": lambda: self.core.list_namespaced_config_map(ns).items,
            "persistentvolumeclaims": lambda: self.core.list_namespaced_persistent_volume_claim(ns).items,
            "networkpolicies": lambda: self.net.list_namespaced_network_policy(ns).items,
            "horizontalpodautoscalers": lambda: autoscaling.list_namespaced_horizontal_pod_autoscaler(ns).items,
            "resourcequotas": lambda: self.core.list_namespaced_resource_quota(ns).items,
            "limitranges": lambda: self.core.list_namespaced_limit_range(ns).items,
            "serviceaccounts": lambda: self.core.list_namespaced_service_account(ns).items,
            "events": lambda: self.core.list_namespaced_event(ns).items,
        }[kind]()

    @staticmethod
    def _summary(kind: str, o) -> str:
        n = o.metadata.name
        age = _age(o.metadata.creation_timestamp)
        if kind == "pods":
            cs = o.status.container_statuses or []
            ready = sum(bool(c.ready) for c in cs)
            restarts = sum(c.restart_count or 0 for c in cs)
            reason = next((c.state.waiting.reason for c in cs if c.state and c.state.waiting), "")
            return f"{n} {ready}/{len(cs)} {o.status.phase} {reason} restarts={restarts} age={age}s"
        if kind == "deployments":
            s = o.status
            return (f"{n} replicas={o.spec.replicas} updated={s.updated_replicas or 0} ready={s.ready_replicas or 0} "
                    f"revision={(o.metadata.annotations or {}).get(REVISION_ANN)} age={age}s")
        if kind == "replicasets":
            return (f"{n} revision={(o.metadata.annotations or {}).get(REVISION_ANN)} desired={o.spec.replicas} "
                    f"ready={o.status.ready_replicas or 0} images={[c.image for c in o.spec.template.spec.containers]} age={age}s")
        if kind == "services":
            return f"{n} selector={o.spec.selector} ports={[p.port for p in o.spec.ports or []]}"
        if kind == "endpoints":
            ready = sum(len(s.addresses or []) for s in o.subsets or [])
            return f"{n} ready_addresses={ready}"
        if kind == "configmaps":
            return f"{n} keys={sorted((o.data or {}).keys())} age={age}s"
        if kind == "networkpolicies":
            return f"{n} podSelector={o.spec.pod_selector.match_labels} types={o.spec.policy_types} age={age}s"
        if kind == "events":
            return f"{o.type} {o.involved_object.kind}/{o.involved_object.name} {o.reason}: {(o.message or '')[:120]}"
        return f"{n} age={age}s"   # other kinds: name and age; details via k8s_describe

    def k8s_get(self, kind: str) -> dict:
        if kind not in GENERIC_KINDS:
            return {"error": f"kind {kind!r} not allowed"}
        items = self._list(kind)
        if kind == "events":
            items = sorted(items, key=lambda e: (e.last_timestamp or e.event_time or e.metadata.creation_timestamp),
                           reverse=True)[:15]
        return {"kind": kind, "items": [self._summary(kind, o) for o in items][:30]}

    def k8s_describe(self, kind: str, name: str) -> dict:
        if kind not in GENERIC_KINDS or kind == "events":
            return {"error": f"kind {kind!r} not allowed"}
        obj = next((o for o in self._list(kind) if o.metadata.name == name), None)
        if obj is None:
            return {"error": f"{kind}/{name} not found"}
        d = client.ApiClient().sanitize_for_serialization(obj)
        meta = d.get("metadata", {})
        for noisy in ("managedFields", "uid", "resourceVersion", "selfLink"):
            meta.pop(noisy, None)
        (meta.get("annotations") or {}).pop("kubectl.kubernetes.io/last-applied-configuration", None)
        return {"kind": kind, "object": d}

    def get_recent_warning_events(self) -> dict:
        evs = self.core.list_namespaced_event(self.namespace, field_selector="type=Warning").items
        now = time.time()
        rows = []
        for e in evs:
            ts = e.last_timestamp or e.event_time or e.metadata.creation_timestamp
            if ts is None or now - ts.timestamp() > 600:
                continue
            rows.append((ts.timestamp(), f"{e.involved_object.kind}/{e.involved_object.name} "
                                         f"{e.reason}: {(e.message or '')[:160]}"))
        rows.sort(reverse=True)
        return {"warning_events": [r for _, r in rows[:15]]}
