#!/usr/bin/env bash
# Deploys Online Boutique (namespace `boutique`) and a second agent instance
# scoped to it, into the existing Kind cluster from setup/setup_kind_cluster.sh.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$HERE/../.."
NS=boutique

kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
python3 "$HERE/make_manifest.py"
kubectl apply -f "$HERE/boutique.yaml" >/dev/null

# same agent manifests, re-targeted at the boutique namespace
LLM_IP="$(docker inspect ollama -f '{{.NetworkSettings.Networks.kind.IPAddress}}')"
for f in 01-rbac.yaml 03-agent-deployment.yaml; do
  sed "s/agentic-demo/$NS/g" "$ROOT/manifests/$f" | kubectl apply -f - >/dev/null
done
sed "s/\${LLM_IP}/${LLM_IP}/; s/agentic-demo/$NS/g" "$ROOT/manifests/04-llm-endpoint.yaml.tpl" | kubectl apply -f - >/dev/null
kubectl scale deployment/remediation-agent -n "$NS" --replicas=0 >/dev/null

for d in $(kubectl get deploy -n "$NS" -o jsonpath='{.items[*].metadata.name}'); do
  [ "$d" = "remediation-agent" ] && continue
  kubectl rollout status deployment/"$d" -n "$NS" --timeout=600s
done
echo "Online Boutique ready; storefront at http://<node-ip>:30080/"
