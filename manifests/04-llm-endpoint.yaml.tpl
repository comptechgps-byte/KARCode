# Selector-less Service that gives in-cluster pods a stable DNS name
# (llm.agentic-demo.svc) for the Ollama container running next to the Kind
# nodes on the `kind` Docker network. setup/setup_kind_cluster.sh fills in
# ${LLM_IP} and applies it.
apiVersion: v1
kind: Service
metadata:
  name: llm
  namespace: agentic-demo
spec:
  ports: [{ name: http, port: 11434, targetPort: 11434 }]
---
apiVersion: v1
kind: Endpoints
metadata:
  name: llm
  namespace: agentic-demo
subsets:
  - addresses: [{ ip: "${LLM_IP}" }]
    ports: [{ name: http, port: 11434 }]
