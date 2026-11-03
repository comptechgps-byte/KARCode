"""
telemetry.py — collects the raw signals the diagnosis stage reasons over:
pod phase, container state *and last state* (waiting/terminated reasons,
exit codes, restart counts), recent Events for the pod, and a tail of
container logs. Kept deliberately close to what `kubectl describe pod`
shows, since that is the ground truth an SRE would look at.

Every signal here is read from the Kubernetes API; nothing is inferred from
names of chaos objects or other benchmark-specific knowledge.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from kubernetes import client

# Waiting reasons that mean the container cannot currently run at all.
BLOCKING_WAITING_REASONS = {
    "CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "InvalidImageName",
    "CreateContainerConfigError", "CreateContainerError", "RunContainerError",
}
# A not-ready, never-restarted pod is only treated as unhealthy once it has
# been running this long, so normal start-up is never mistaken for a fault.
STARTUP_GRACE_S = 10.0


@dataclass
class ContainerState:
    name: str
    ready: bool
    restart_count: int
    waiting_reason: Optional[str] = None
    waiting_message: Optional[str] = None
    terminated_reason: Optional[str] = None
    exit_code: Optional[int] = None
    last_terminated_reason: Optional[str] = None
    last_exit_code: Optional[int] = None


@dataclass
class PodSnapshot:
    name: str
    namespace: str
    phase: str
    node_name: Optional[str]
    labels: dict = field(default_factory=dict)
    owner_deployment: Optional[str] = None
    start_time: Optional[float] = None
    containers: list[ContainerState] = field(default_factory=list)
    recent_events: list[str] = field(default_factory=list)
    log_tail: str = ""
    unhealthy_reason: Optional[str] = None
    first_seen_unhealthy_at: Optional[float] = None
    change_context: dict = field(default_factory=dict)   # "what changed recently?"


def owner_deployment_of(pod: client.V1Pod) -> Optional[str]:
    """Deployment -> ReplicaSet -> Pod naming is deterministic: the RS is
    named '<deployment>-<pod-template-hash>'. Resolving it this way needs no
    extra API call (v1 read the ReplicaSet without RBAC permission, failed
    silently, and fell back to a pod restart on every OOM)."""
    template_hash = (pod.metadata.labels or {}).get("pod-template-hash")
    for owner in pod.metadata.owner_references or []:
        if owner.kind == "ReplicaSet" and template_hash and owner.name.endswith("-" + template_hash):
            return owner.name[: -len(template_hash) - 1]
    return None


def _ts(dt) -> Optional[float]:
    return dt.timestamp() if dt is not None else None


class TelemetryCollector:
    def __init__(self, core_api: client.CoreV1Api, namespace: str,
                 apps_api: Optional[client.AppsV1Api] = None):
        self.core = core_api
        self.apps = apps_api
        self.namespace = namespace
        self._unhealthy_since: dict[str, float] = {}

    def list_pods(self) -> list[client.V1Pod]:
        pods = self.core.list_namespaced_pod(self.namespace).items
        return [p for p in pods if p.metadata.deletion_timestamp is None]

    def read_pod(self, name: str) -> Optional[client.V1Pod]:
        try:
            pod = self.core.read_namespaced_pod(name, self.namespace)
        except client.ApiException:
            return None
        return None if pod.metadata.deletion_timestamp is not None else pod

    def snapshot(self, pod: client.V1Pod, with_context: bool = True) -> PodSnapshot:
        """with_context=False skips the (expensive) log tail and must not
        change the health verdict."""
        name = pod.metadata.name
        containers = [self._container_state(cs) for cs in (pod.status.container_statuses or [])]
        # Events are part of the health definition (dependency rule), so they
        # are always read; `with_context` only controls the log tail.
        events = self._recent_events(name)
        reason = self.unhealthy_reason(pod, containers, events)

        if reason and name not in self._unhealthy_since:
            self._unhealthy_since[name] = time.time()
        if not reason:
            self._unhealthy_since.pop(name, None)

        snap = PodSnapshot(
            name=name,
            namespace=self.namespace,
            phase=pod.status.phase,
            node_name=pod.spec.node_name,
            labels=dict(pod.metadata.labels or {}),
            owner_deployment=owner_deployment_of(pod),
            start_time=_ts(pod.status.start_time),
            containers=containers,
            recent_events=events,
            unhealthy_reason=reason,
            first_seen_unhealthy_at=self._unhealthy_since.get(name),
        )
        if reason and with_context:
            snap.log_tail = self._log_tail(name, containers)
            snap.change_context = self._change_context(snap.owner_deployment)
        return snap

    def _change_context(self, deployment: Optional[str]) -> dict:
        """Generic change correlation: the owning Deployment's current
        revision, how long ago it was rolled out, and whether an older
        revision exists to roll back to."""
        if not deployment or self.apps is None:
            return {}
        try:
            dep = self.apps.read_namespaced_deployment(deployment, self.namespace)
            rss = self.apps.list_namespaced_replica_set(self.namespace).items
        except client.ApiException:
            return {}
        ann = "deployment.kubernetes.io/revision"
        cur = int((dep.metadata.annotations or {}).get(ann, "0"))
        revs = {}
        for rs in rss:
            if any(o.kind == "Deployment" and o.uid == dep.metadata.uid
                   for o in rs.metadata.owner_references or []):
                revs[int((rs.metadata.annotations or {}).get(ann, "0"))] = rs
        ctx = {"deployment_revision": cur,
               "previous_revision_available": any(0 < r < cur for r in revs)}
        if cur in revs:
            ctx["seconds_since_current_revision_rolled_out"] = round(
                time.time() - revs[cur].metadata.creation_timestamp.timestamp())
        return ctx

    # ------------------------------------------------------------------ #
    @staticmethod
    def _container_state(cs) -> ContainerState:
        st, last = cs.state, cs.last_state
        return ContainerState(
            name=cs.name,
            ready=bool(cs.ready),
            restart_count=cs.restart_count or 0,
            waiting_reason=st.waiting.reason if st and st.waiting else None,
            waiting_message=(st.waiting.message or "")[:300] if st and st.waiting else None,
            terminated_reason=st.terminated.reason if st and st.terminated else None,
            exit_code=st.terminated.exit_code if st and st.terminated else None,
            last_terminated_reason=last.terminated.reason if last and last.terminated else None,
            last_exit_code=last.terminated.exit_code if last and last.terminated else None,
        )

    @staticmethod
    def unhealthy_reason(pod: client.V1Pod, containers: list[ContainerState],
                         events: list[str]) -> Optional[str]:
        """Returns a short machine-readable reason, or None if healthy."""
        if pod.status.phase in ("Failed", "Unknown"):
            return f"phase-{pod.status.phase.lower()}"
        for c in containers:
            if c.waiting_reason in BLOCKING_WAITING_REASONS:
                return f"waiting-{c.waiting_reason}"
            if c.terminated_reason == "OOMKilled" or (
                    not c.ready and c.last_terminated_reason == "OOMKilled"):
                return "oom-killed"
            if not c.ready and c.restart_count > 0:
                return "not-ready-after-restart"
        # Dependency-type fault: running, never restarted, not ready, with an
        # explicit readiness-probe failure event and past start-up grace.
        if (pod.status.phase == "Running" and containers
                and all(c.restart_count == 0 for c in containers)
                and any(not c.ready for c in containers)
                and any("readiness probe failed" in e.lower() for e in events)):
            started = _ts(pod.status.start_time)
            if started is None or time.time() - started > STARTUP_GRACE_S:
                return "readiness-failing-no-restart"
        return None

    def _recent_events(self, pod_name: str, limit: int = 6) -> list[str]:
        try:
            events = self.core.list_namespaced_event(
                self.namespace, field_selector=f"involvedObject.name={pod_name}"
            ).items
        except client.ApiException:
            return []
        events.sort(key=lambda e: _ts(e.last_timestamp or e.event_time or e.metadata.creation_timestamp) or 0,
                    reverse=True)
        return [f"{e.type}/{e.reason}: {(e.message or '')[:240]}" for e in events[:limit]]

    def _log_tail(self, pod_name: str, containers: list[ContainerState], lines: int = 30) -> str:
        if not containers:
            return ""
        for previous in (True, False):
            try:
                return self.core.read_namespaced_pod_log(
                    pod_name, self.namespace, container=containers[0].name,
                    tail_lines=lines, previous=previous,
                )
            except client.ApiException:
                continue
        return ""
