#!/usr/bin/env bash
# Creates the Kind testbed (1 control plane + 2 workers), starts the local
# LLM server (Ollama) on the same Docker network, builds/loads the agent
# image and deploys everything. Idempotent. Run natively, or inside the
# runner image (tools/runner, see README "Reproducing the paper").
set -euo pipefail

CLUSTER="agentic-remediator"
NODE_IMAGE="${NODE_IMAGE:-kindest/node:v1.33.1}"
LLM_MODEL="${LLM_MODEL:-qwen2.5:7b-instruct}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  cat > /tmp/kind-config.yaml <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: ${CLUSTER}
nodes:
  - role: control-plane
  - role: worker
    labels: { node-role.demo/zone: cloud }
  - role: worker
    labels: { node-role.demo/zone: edge }
EOF
  kind create cluster --image "$NODE_IMAGE" --config /tmp/kind-config.yaml
fi
# inside a container on the `kind` network, talk to the API server directly
if [ -f /.dockerenv ]; then
  mkdir -p ~/.kube && kind get kubeconfig --internal --name "$CLUSTER" > ~/.kube/config
fi
kubectl wait --for=condition=Ready nodes --all --timeout=180s

# --- local LLM server --------------------------------------------------------
if ! docker ps --format '{{.Names}}' | grep -qx ollama; then
  docker run -d --name ollama --network kind -v ollama:/root/.ollama \
    -e OLLAMA_KEEP_ALIVE=24h -e OLLAMA_NUM_PARALLEL=1 ollama/ollama:latest
  sleep 3
fi
docker exec ollama ollama pull "$LLM_MODEL"
LLM_IP="$(docker inspect ollama -f '{{.NetworkSettings.Networks.kind.IPAddress}}')"

# --- agent image ------------------------------------------------------------
docker build -t k8s-agentic-remediator:local -f "$ROOT/docker/Dockerfile" "$ROOT"
kind load docker-image k8s-agentic-remediator:local --name "$CLUSTER"

# --- deploy -----------------------------------------------------------------
kubectl apply -f "$ROOT/manifests/00-namespace.yaml"
kubectl apply -f "$ROOT/manifests/01-rbac.yaml"
kubectl apply -f "$ROOT/manifests/02-demo-services.yaml"
kubectl apply -f "$ROOT/manifests/02-demo-app.yaml"
sed "s/\${LLM_IP}/${LLM_IP}/" "$ROOT/manifests/04-llm-endpoint.yaml.tpl" | kubectl apply -f -
kubectl apply -f "$ROOT/manifests/03-agent-deployment.yaml"
kubectl set env deployment/remediation-agent -n agentic-demo LLM_MODEL="$LLM_MODEL" >/dev/null
kubectl scale deployment/remediation-agent -n agentic-demo --replicas=0
for d in frontend backend cache-proxy; do
  kubectl rollout status deployment/$d -n agentic-demo --timeout=120s
done
echo "Testbed ready (LLM at ${LLM_IP}:11434, model ${LLM_MODEL}). Agent scaled to 0; the benchmark starts it per trial."
