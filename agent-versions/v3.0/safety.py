"""
safety.py — the approval gate between "the agent decided X" and "X actually
happens to the cluster". Checks, in order (first failure rejects):

  1. Known action.   Unknown action names are treated as destructive.
  2. Scope.          The target must be the diagnosed pod or its owning
                     Deployment. An LLM (or a prompt-injected log line) cannot
                     redirect an action at another workload, node or namespace.
  3. Budget.         At most ACTION_BUDGET executed actions per Deployment per
                     BUDGET_WINDOW_S. Bounds the blast radius of a wrong or
                     oscillating diagnosis (v1 issued 40 pod deletions in 90 s
                     against one OOM-looping Deployment).
  4. Risk tier.      safe        -> auto-approved
                     moderate    -> approval callback, or AUTO_APPROVE_MODERATE
                                    (unattended benchmark runs only)
                     destructive -> rejected unless ALLOW_DESTRUCTIVE=true,
                                    then still needs the approval callback.
"""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Callable, Optional

RISK_SAFE = "safe"
RISK_MODERATE = "moderate"
RISK_DESTRUCTIVE = "destructive"

ACTION_RISK = {
    "noop": RISK_SAFE,
    "restart-pod": RISK_SAFE,
    "patch-limits": RISK_SAFE,              # bounded by MAX_MEMORY in remediation.py
    "rollback-deployment": RISK_MODERATE,   # changes the running release
    "scale-deployment": RISK_MODERATE,
    "revert-network-policy": RISK_MODERATE, # changes security posture
    "cordon-node": RISK_DESTRUCTIVE,
    "drain-node": RISK_DESTRUCTIVE,
    "delete-pvc": RISK_DESTRUCTIVE,
}


@dataclass
class ApprovalResult:
    approved: bool
    reason: str
    risk: str
    check: str          # which check decided: action|scope|budget|tier


ApprovalCallback = Callable[[str, str, str], bool]  # (action, target, reasoning) -> bool


def _cli_prompt_callback(action: str, target: str, reasoning: str) -> bool:
    print(f"\n[APPROVAL REQUIRED] action={action} target={target}\nreasoning={reasoning}")
    return input("Approve? [y/N]: ").strip().lower() == "y"


def _env_bool(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).lower() == "true"


class SafetyGate:
    def __init__(
        self,
        namespace: str = "default",
        allow_destructive: Optional[bool] = None,
        auto_approve_moderate: Optional[bool] = None,
        approval_callback: Optional[ApprovalCallback] = None,
        action_budget: Optional[int] = None,
        budget_window_s: Optional[float] = None,
    ):
        self.namespace = namespace
        self.allow_destructive = (allow_destructive if allow_destructive is not None
                                  else _env_bool("ALLOW_DESTRUCTIVE"))
        self.auto_approve_moderate = (auto_approve_moderate if auto_approve_moderate is not None
                                      else _env_bool("AUTO_APPROVE_MODERATE"))
        self.approval_callback = approval_callback or _cli_prompt_callback
        self.action_budget = action_budget or int(os.environ.get("ACTION_BUDGET", "3"))
        self.budget_window_s = budget_window_s or float(os.environ.get("BUDGET_WINDOW_S", "600"))
        self._history: dict[str, deque] = defaultdict(deque)

    def normalise_target(self, target: str) -> str:
        t = (target or "").strip()
        for prefix in (f"{self.namespace}/", f"deployment/{self.namespace}/", "deployment/",
                       "deployments/", "pod/", "pods/"):
            if t.startswith(prefix):
                t = t[len(prefix):]
        return t

    def evaluate(self, action: str, target: str, reasoning: str,
                 pod: str, deployment: Optional[str]) -> ApprovalResult:
        risk = ACTION_RISK.get(action, RISK_DESTRUCTIVE)
        if action not in ACTION_RISK:
            return ApprovalResult(False, f"unknown action {action!r}", risk, "action")
        if action == "noop":
            return ApprovalResult(True, "noop", risk, "tier")

        allowed = {pod} | ({deployment} if deployment else set())
        if self.normalise_target(target) not in allowed:
            return ApprovalResult(False, f"target {target!r} outside scope {sorted(allowed)}",
                                  risk, "scope")

        key = deployment or pod
        window = self._history[key]
        now = time.time()
        while window and now - window[0] > self.budget_window_s:
            window.popleft()
        if len(window) >= self.action_budget:
            return ApprovalResult(False, f"action budget exhausted for {key} "
                                  f"({self.action_budget}/{self.budget_window_s:.0f}s)", risk, "budget")

        if risk == RISK_SAFE:
            return ApprovalResult(True, "safe action, auto-approved", risk, "tier")
        if risk == RISK_DESTRUCTIVE:
            if not self.allow_destructive:
                return ApprovalResult(False, "destructive actions disabled (ALLOW_DESTRUCTIVE=false)",
                                      risk, "tier")
            return ApprovalResult(self.approval_callback(action, target, reasoning),
                                  "human/callback decision (destructive)", risk, "tier")
        if self.auto_approve_moderate:
            return ApprovalResult(True, "moderate action, auto-approved (benchmark mode)", risk, "tier")
        return ApprovalResult(self.approval_callback(action, target, reasoning),
                              "human/callback decision (moderate)", risk, "tier")

    def record_execution(self, pod: str, deployment: Optional[str]) -> None:
        self._history[deployment or pod].append(time.time())
