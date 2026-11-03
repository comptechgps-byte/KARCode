#!/usr/bin/env bash
# Testbed preflight — run before any experiment, and always after a host or
# Docker restart. Each check guards against a failure seen during the experiments:
#   1. all nodes Ready
#   2. no pod with an IP outside its node's pod CIDR (stale after node-IP changes)
#   3. cluster DNS resolves from every node
#   4. NetworkPolicy enforcement takes effect within 20 s (kindnet can come back
#      from a Docker restart with enforcement silently broken; fix: restart kindnet)
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0
kubectl wait --for=condition=Ready nodes --all --timeout=180s >/dev/null && echo "ok   nodes Ready" || { echo "FAIL nodes"; fail=1; }

python3 - <<'EOF' || fail=1
import ipaddress, sys
from kubernetes import client, config
config.load_kube_config(); v1 = client.CoreV1Api()
cidr = {n.metadata.name: ipaddress.ip_network(n.spec.pod_cidr) for n in v1.list_node().items}
bad = [f"{p.metadata.namespace}/{p.metadata.name}" for p in v1.list_pod_for_all_namespaces().items
       if p.status.pod_ip and p.spec.node_name in cidr and not p.spec.host_network
       and ipaddress.ip_address(p.status.pod_ip) not in cidr[p.spec.node_name]]
print(("FAIL stale pod IPs: " + ", ".join(bad)) if bad else "ok   no stale pod IPs")
sys.exit(1 if bad else 0)
EOF

for n in $(kubectl get nodes -o jsonpath='{.items[*].metadata.name}'); do
  # capture first: with pipefail, `grep -q` closing the pipe early would fail the pipeline
  out=$(kubectl run "dns-$RANDOM" -n agentic-demo --rm -i --restart=Never --image=busybox:1.36 \
        --overrides="{\"spec\":{\"nodeName\":\"$n\"}}" -- nslookup -timeout=3 kubernetes.default.svc.cluster.local 2>&1)
  if grep -q '^Address: 10\.96\.' <<<"$out"; then echo "ok   DNS from $n"; else echo "FAIL DNS from $n"; fail=1; fi
done

netpol_test() {   # 0 if a deny-egress policy makes cache-proxy unready within 20 s
  bash chaos/inject_faults.sh reset >/dev/null && bash chaos/inject_faults.sh net-partition >/dev/null
  local seen=""
  for i in $(seq 1 20); do
    [ "$(kubectl get pods -n agentic-demo -l app=cache-proxy -o jsonpath='{.items[0].status.containerStatuses[0].ready}')" = "false" ] && { seen=$i; break; }
    sleep 1
  done
  bash chaos/inject_faults.sh reset >/dev/null
  [ -n "$seen" ]
}
ok=0; for t in 1 2 3; do netpol_test && ok=$((ok + 1)); done
if [ "$ok" != 3 ] && [ "${1:-}" = "--fix" ]; then
  # kindnet's NetworkPolicy enforcement is intermittently lost on this host;
  # restart it and require three consecutive successful enforcement tests
  echo "info NetworkPolicy enforced in $ok/3 tests; restarting kindnet"
  kubectl rollout restart daemonset/kindnet -n kube-system >/dev/null
  kubectl rollout status daemonset/kindnet -n kube-system --timeout=180s >/dev/null
  sleep 10
  ok=0; for t in 1 2 3; do netpol_test && ok=$((ok + 1)); done
fi
[ "$ok" = 3 ] && echo "ok   NetworkPolicy enforced in 3/3 tests" || { echo "FAIL NetworkPolicy enforced in only $ok/3 tests (try --fix)"; fail=1; }
exit $fail
