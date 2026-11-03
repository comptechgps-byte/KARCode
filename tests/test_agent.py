"""Unit tests for the agent's decision logic (no cluster needed).

    python -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))

from diagnosis import DiagnosisEngine  # noqa: E402
from llm import LLMResult  # noqa: E402
from remediation import format_mib, parse_quantity, selector_matches  # noqa: E402
from safety import SafetyGate  # noqa: E402
from telemetry import ContainerState, PodSnapshot, owner_deployment_of  # noqa: E402


def snap(reason, **c):
    cs = ContainerState(name="app", ready=c.pop("ready", False), restart_count=c.pop("restarts", 0), **c)
    return PodSnapshot(name="backend-abc-123", namespace="ns", phase="Running", node_name="n1",
                       owner_deployment="backend", containers=[cs], unhealthy_reason=reason)


class FakeLLM:
    def __init__(self, reply: dict | str, fail: bool = False):
        self.reply, self.fail, self.calls = reply, fail, 0

    def complete(self, system, user, schema=None):
        self.calls += 1
        if self.fail:
            raise TimeoutError("timed out")
        text = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return LLMResult(text=text, latency_s=1.5, prompt_tokens=100, completion_tokens=20)


class RuleEngineTest(unittest.TestCase):
    def rules(self, s):
        return DiagnosisEngine("rules").diagnose(s)

    def test_oom_in_last_state_beats_crashloop(self):
        d = self.rules(snap("waiting-CrashLoopBackOff", waiting_reason="CrashLoopBackOff",
                            restarts=3, last_terminated_reason="OOMKilled", last_exit_code=137))
        self.assertEqual(d.proposed_action, "patch-limits")
        self.assertEqual(d.action_target, "backend")

    def test_persistent_crash_rolls_back(self):
        d = self.rules(snap("not-ready-after-restart", terminated_reason="Error", exit_code=1, restarts=2))
        self.assertEqual(d.proposed_action, "rollback-deployment")

    def test_single_crash_is_deferred_not_escalated(self):
        llm = FakeLLM({})
        d = DiagnosisEngine("hybrid", llm).diagnose(
            snap("waiting-CrashLoopBackOff", waiting_reason="CrashLoopBackOff", restarts=1))
        self.assertEqual((d.fault_class, d.proposed_action, llm.calls),
                         ("suspected-transient", "noop", 0))

    def test_image_pull_is_not_actionable(self):
        d = self.rules(snap("waiting-ImagePullBackOff", waiting_reason="ImagePullBackOff"))
        self.assertEqual(d.proposed_action, "noop")
        self.assertFalse(d.actionable)

    def test_config_error_has_no_rule(self):
        self.assertIsNone(self.rules(snap("waiting-CreateContainerConfigError",
                                          waiting_reason="CreateContainerConfigError")))

    def test_dependency_signature(self):
        d = self.rules(snap("readiness-failing-no-restart"))
        self.assertEqual(d.proposed_action, "revert-network-policy")


class HybridTest(unittest.TestCase):
    def test_rule_hit_does_not_call_llm(self):
        llm = FakeLLM({})
        d = DiagnosisEngine("hybrid", llm).diagnose(
            snap("oom-killed", terminated_reason="OOMKilled", restarts=1))
        self.assertEqual((d.source, llm.calls), ("rules", 0))

    def test_non_actionable_rule_escalates_with_hint(self):
        llm = FakeLLM({"fault_class": "bad-release", "confidence": 0.8, "reasoning": "x",
                       "proposed_action": "rollback-deployment", "action_target": "backend"})
        d = DiagnosisEngine("hybrid", llm).diagnose(
            snap("waiting-ImagePullBackOff", waiting_reason="ImagePullBackOff"))
        self.assertEqual((d.source, d.proposed_action, llm.calls), ("llm", "rollback-deployment", 1))
        self.assertIn("image-pull-error", d.rule_hint)

    # v3.1: failure-triggered escalation
    def test_failed_rule_action_escalates_with_failure_in_hint(self):
        llm = FakeLLM({"fault_class": "bad-release", "confidence": 0.8, "reasoning": "x",
                       "proposed_action": "rollback-deployment", "action_target": "backend"})
        d = DiagnosisEngine("hybrid", llm).diagnose(
            snap("readiness-failing-no-restart"), rule_failure="no NetworkPolicy newer than the pod")
        self.assertEqual((d.source, d.proposed_action, llm.calls), ("llm", "rollback-deployment", 1))
        self.assertIn("already tried and failed", d.rule_hint)

    def test_without_failure_rule_still_wins(self):
        llm = FakeLLM({})
        d = DiagnosisEngine("hybrid", llm).diagnose(snap("readiness-failing-no-restart"), rule_failure=None)
        self.assertEqual((d.source, d.proposed_action, llm.calls), ("rules", "revert-network-policy", 0))

    def test_rules_mode_never_escalates_on_failure(self):
        llm = FakeLLM({})
        d = DiagnosisEngine("rules", llm).diagnose(snap("readiness-failing-no-restart"), rule_failure="x")
        self.assertEqual((d.source, llm.calls), ("rules", 0))

    def test_deferral_unaffected_by_failure(self):
        llm = FakeLLM({})
        d = DiagnosisEngine("hybrid", llm).diagnose(
            snap("waiting-CrashLoopBackOff", waiting_reason="CrashLoopBackOff", restarts=1), rule_failure="x")
        self.assertEqual((d.proposed_action, llm.calls), ("noop", 0))

    def test_llm_failure_falls_back_to_rule(self):
        d = DiagnosisEngine("hybrid", FakeLLM({}, fail=True)).diagnose(
            snap("waiting-ImagePullBackOff", waiting_reason="ImagePullBackOff"))
        self.assertEqual((d.source, d.proposed_action), ("rules+llm-fallback", "noop"))
        self.assertIn("error", d.llm)

    def test_malformed_llm_output_degrades_to_noop(self):
        d = DiagnosisEngine("llm-only", FakeLLM("not json")).diagnose(snap("oom-killed"))
        self.assertEqual(d.proposed_action, "noop")

    def test_restart_mode(self):
        d = DiagnosisEngine("restart").diagnose(snap("oom-killed"))
        self.assertEqual((d.proposed_action, d.action_target), ("restart-pod", "backend-abc-123"))


class FakeChat:
    """Ollama-style chat client: scripted assistant messages."""
    def __init__(self, script):
        self.script, self.calls, self.seen = list(script), 0, []

    def chat(self, messages, tools=None, schema=None):
        self.calls += 1
        self.seen.append((len(messages), bool(tools), schema is not None))
        msg = self.script.pop(0)
        return msg, LLMResult(text=msg.get("content", ""), latency_s=1.0, prompt_tokens=10,
                              completion_tokens=5)


class FakeTools:
    def __init__(self):
        self.calls = []

    def call(self, name, args):
        self.calls.append((name, args))
        return json.dumps({"configmaps": {"backend-flags-v2": "MISSING"}})


FINAL = {"content": json.dumps({"fault_class": "missing-config", "confidence": 0.9, "reasoning": "x",
                                "proposed_action": "rollback-deployment", "action_target": "backend"})}


def tool_msg(name, **args):
    return {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


class ToolModeTest(unittest.TestCase):
    def test_hybrid_tools_escalates_uses_tools_then_decides(self):
        llm, tools = FakeChat([tool_msg("check_config_references", deployment="backend"),
                               {"content": "done"}, FINAL]), FakeTools()
        d = DiagnosisEngine("hybrid-tools", llm, tools=tools).diagnose(
            snap("waiting-CreateContainerConfigError", waiting_reason="CreateContainerConfigError"))
        self.assertEqual(d.proposed_action, "rollback-deployment")
        self.assertEqual(tools.calls, [("check_config_references", {"deployment": "backend"})])
        self.assertEqual(d.llm["tools"], ["check_config_references"])
        self.assertTrue(llm.seen[-1][2])  # final call is schema-constrained

    def test_tool_calls_are_capped(self):
        # the model keeps asking for tools; after 3 calls the loop must stop and ask for the decision
        llm = FakeChat([tool_msg("get_recent_warning_events")] * 3 + [FINAL])
        tools = FakeTools()
        d = DiagnosisEngine("llm-tools", llm, tools=tools).diagnose(snap("oom-killed"))
        self.assertEqual(len(tools.calls), 3)
        self.assertEqual(d.proposed_action, "rollback-deployment")

    def test_rule_hit_skips_llm_in_hybrid_tools(self):
        llm = FakeChat([])
        d = DiagnosisEngine("hybrid-tools", llm, tools=FakeTools()).diagnose(
            snap("oom-killed", terminated_reason="OOMKilled", restarts=1))
        self.assertEqual((d.source, llm.calls), ("rules", 0))

    def test_generic_mode_offers_only_generic_tools(self):
        seen_specs = []

        class SpecChat(FakeChat):
            def chat(self, messages, tools=None, schema=None):
                seen_specs.append(tools)
                return super().chat(messages, tools, schema)

        llm = SpecChat([tool_msg("k8s_get", kind="replicasets"), {"content": "done"}, FINAL])
        tools = FakeTools()
        d = DiagnosisEngine("hybrid-generic", llm, tools=tools).diagnose(
            snap("waiting-CreateContainerConfigError", waiting_reason="CreateContainerConfigError"))
        names = {t["function"]["name"] for t in seen_specs[0]}
        self.assertEqual(names, {"k8s_get", "k8s_describe"})
        self.assertEqual(tools.calls, [("k8s_get", {"kind": "replicasets"})])
        self.assertEqual(d.llm["tools"], ["k8s_get:replicasets"])
        self.assertEqual(d.proposed_action, "rollback-deployment")

    def test_generic_tool_specs_name_no_fault_class(self):
        from tools import GENERIC_TOOL_SPECS
        text = json.dumps(GENERIC_TOOL_SPECS).lower()
        for fault_word in ("rollout", "network_polic", "config_reference", "secret", "oom", "crash"):
            self.assertNotIn(fault_word, text.replace("networkpolicies", ""))

    def test_tools_mode_without_tools_degrades_to_noop(self):
        d = DiagnosisEngine("llm-tools", FakeChat([])).diagnose(snap("oom-killed"))
        self.assertEqual(d.proposed_action, "noop")


class LastHealthyRollbackTest(unittest.TestCase):
    def test_rollback_skips_broken_intermediate_revisions(self):
        from remediation import RemediationExecutor
        dep = NS(metadata=NS(uid="u1", annotations={"deployment.kubernetes.io/revision": "3"}))

        from kubernetes import client as k

        def rs(rev, h):
            tpl = k.V1PodTemplateSpec(metadata=k.V1ObjectMeta(labels={"pod-template-hash": h}),
                                      spec=k.V1PodSpec(containers=[k.V1Container(name="c", image=h)]))
            return NS(metadata=NS(annotations={"deployment.kubernetes.io/revision": str(rev)},
                                  owner_references=[NS(kind="Deployment", uid="u1")]),
                      spec=NS(template=tpl))
        patched = []
        apps = NS(read_namespaced_deployment=lambda n, ns: dep,
                  list_namespaced_replica_set=lambda ns: NS(items=[rs(1, "good"), rs(2, "bad"), rs(3, "worse")]),
                  patch_namespaced_deployment=lambda n, ns, body: patched.append(body))
        ex = RemediationExecutor(None, apps, "ns", networking_api=object(),
                                 healthy_hashes={"backend": {"good"}})
        res = ex.rollback_deployment(snap("x"))
        self.assertTrue(res.ok)
        self.assertIn("to 1 (last observed healthy)", res.detail)
        ex.healthy_hashes.clear()
        self.assertIn("to 2 (previous)", ex.rollback_deployment(snap("x")).detail)


class SafetyGateTest(unittest.TestCase):
    def gate(self, **kw):
        return SafetyGate(namespace="ns", allow_destructive=False, auto_approve_moderate=True,
                          approval_callback=lambda *a: False, **kw)

    def test_destructive_rejected(self):
        r = self.gate().evaluate("drain-node", "backend", "", pod="p", deployment="backend")
        self.assertFalse(r.approved)

    def test_scope_blocks_foreign_target(self):
        for t in ("agentic-remediator-worker", "kube-system/coredns", "frontend"):
            r = self.gate().evaluate("restart-pod", t, "", pod="p", deployment="backend")
            self.assertEqual((r.approved, r.check), (False, "scope"), t)

    def test_scope_accepts_prefixed_own_target(self):
        r = self.gate().evaluate("rollback-deployment", "deployment/ns/backend", "",
                                 pod="p", deployment="backend")
        self.assertTrue(r.approved)

    def test_unknown_action_rejected(self):
        self.assertFalse(self.gate().evaluate("rm-rf", "backend", "", pod="p", deployment="backend").approved)

    def test_budget(self):
        g = self.gate(action_budget=2, budget_window_s=600)
        for _ in range(2):
            self.assertTrue(g.evaluate("restart-pod", "p", "", pod="p", deployment="d").approved)
            g.record_execution("p", "d")
        r = g.evaluate("restart-pod", "p", "", pod="p", deployment="d")
        self.assertEqual((r.approved, r.check), (False, "budget"))

    def test_moderate_needs_approval_outside_benchmark(self):
        g = SafetyGate(namespace="ns", allow_destructive=False, auto_approve_moderate=False,
                       approval_callback=lambda *a: False)
        self.assertFalse(g.evaluate("rollback-deployment", "d", "", pod="p", deployment="d").approved)


class HelpersTest(unittest.TestCase):
    def test_quantities(self):
        self.assertEqual(parse_quantity("64Mi"), 64 * 2**20)
        self.assertEqual(parse_quantity("1Gi"), 2**30)
        self.assertEqual(parse_quantity("100M"), 10**8)
        self.assertEqual(format_mib(128 * 2**20), "128Mi")

    def test_selector(self):
        sel = NS(match_labels={"tier": "cache"}, match_expressions=None)
        self.assertTrue(selector_matches(sel, {"tier": "cache", "app": "x"}))
        self.assertFalse(selector_matches(sel, {"tier": "api"}))
        self.assertTrue(selector_matches(NS(match_labels=None, match_expressions=None), {}))
        expr = NS(match_labels=None, match_expressions=[NS(key="app", operator="In", values=["a"])])
        self.assertFalse(selector_matches(expr, {"app": "b"}))

    def test_owner_resolution(self):
        pod = NS(metadata=NS(labels={"pod-template-hash": "7d9f"},
                             owner_references=[NS(kind="ReplicaSet", name="cache-proxy-7d9f")]))
        self.assertEqual(owner_deployment_of(pod), "cache-proxy")


if __name__ == "__main__":
    unittest.main()
