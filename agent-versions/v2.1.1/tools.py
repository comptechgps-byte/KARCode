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
              "get_recent_warning_events": self.get_recent_warning_events}.get(name)
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
