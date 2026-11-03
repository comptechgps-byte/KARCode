#!/usr/bin/env bash
# Fault injector for the Online Boutique testbed (catalog: faults.yaml).
# Usage: inject.sh <scenario>|reset [target-deployment]
set -euo pipefail
NS="boutique"
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HERE/boutique.yaml"
REG="us-central1-docker.pkg.dev/online-boutique-ci/microservices-demo"

case "${1:-}" in
  bad-image)        # release with a non-existent image tag
    kubectl set image deployment/productcatalogservice -n "$NS" server="$REG/productcatalogservice:v0.10.7-does-not-exist" >/dev/null ;;
  crash-release)    # release whose entrypoint cannot start (process exits 1)
    kubectl patch deployment currencyservice -n "$NS" --type json \
      -p '[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["node","/does-not-exist.js"]}]' >/dev/null ;;
  memory-release)   # release with a memory limit far below the working set
    kubectl set resources deployment/cartservice -n "$NS" -c server --requests=memory=16Mi --limits=memory=16Mi >/dev/null ;;
  missing-secret)   # release referencing a Secret that was never created
    kubectl patch deployment shippingservice -n "$NS" --type json \
      -p '[{"op":"add","path":"/spec/template/spec/containers/0/env/-","value":{"name":"CARRIER_API_KEY","valueFrom":{"secretKeyRef":{"name":"shipping-carrier-credentials","key":"api-key"}}}}]' >/dev/null ;;
  unschedulable)    # release pinned to a node label no node has
    kubectl patch deployment adservice -n "$NS" --type merge \
      -p '{"spec":{"template":{"spec":{"nodeSelector":{"node-role.demo/zone":"gpu"}}}}}' >/dev/null ;;
  pod-kill)         # user-facing pod deleted; ReplicaSet recreates it
    kubectl delete pod -n "$NS" -l app=frontend --wait=false >/dev/null ;;
  selector-break)   # Service no longer selects its pods — RETIRED after validation (latent >180 s)
    kubectl patch service cartservice -n "$NS" --type merge -p '{"spec":{"selector":{"app":"cartservice-v2"}}}' >/dev/null ;;
  zero-replicas)    # config change scales a dependency to zero; no pod is ever unhealthy
    kubectl scale deployment/cartservice -n "$NS" --replicas=0 >/dev/null ;;
  # --- amendment v5 (2026-10-03): two further held-out classes, frozen before any agent run
  nonroot-release)  # release asks to run as root under the pod's runAsNonRoot policy
    kubectl patch deployment paymentservice -n "$NS" --type json \
      -p '[{"op":"add","path":"/spec/template/spec/containers/0/securityContext/runAsUser","value":0}]' >/dev/null ;;
  probe-misconfig)  # release points the readiness probe at a port nothing listens on
    kubectl patch deployment emailservice -n "$NS" --type json \
      -p '[{"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/grpc/port","value":9555}]' >/dev/null ;;
  reset)
    target="${2:-}"
    kubectl delete networkpolicy --all -n "$NS" --ignore-not-found >/dev/null
    # re-create every Deployment whose spec changed since creation (generation > 1),
    # plus the trial's target; untouched Deployments are left running
    for row in $(kubectl get deploy -n "$NS" -o jsonpath='{range .items[*]}{.metadata.name}={.metadata.generation}{" "}{end}'); do
      name="${row%%=*}"; gen="${row##*=}"
      [ "$name" = "remediation-agent" ] && continue   # the harness scales it (bumps generation)
      if [ "$gen" != "1" ] || [ "$name" = "$target" ]; then
        kubectl delete deployment "$name" -n "$NS" --wait=true >/dev/null
      fi
    done
    kubectl apply -f "$APP" >/dev/null          # also restores Service selectors
    for d in $(kubectl get deploy -n "$NS" -o jsonpath='{.items[*].metadata.name}'); do
      [ "$d" = "remediation-agent" ] && continue
      kubectl rollout status deployment/"$d" -n "$NS" --timeout=180s >/dev/null
    done ;;
  *)
    echo "Usage: $0 {bad-image|crash-release|memory-release|missing-secret|unschedulable|pod-kill|selector-break|reset [target]}" >&2
    exit 1 ;;
esac
