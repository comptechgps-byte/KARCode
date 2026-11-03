# Reproducible experiment runner: everything needed to create the Kind
# cluster, drive the benchmark and analyse results, with pinned versions so
# a reviewer can regenerate every number in the paper from one image.
#
#   docker build -t k8s-remediator-runner:1 tools/runner
#   docker run --rm -it --network kind \
#     -v /var/run/docker.sock:/var/run/docker.sock \
#     -v "$PWD":/work -w /work k8s-remediator-runner:1 bash
FROM python:3.11-slim

ARG KIND_VERSION=v0.29.0
ARG KUBECTL_VERSION=v1.33.1

COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates bash jq \
 && rm -rf /var/lib/apt/lists/* \
 && curl -fsSLo /usr/local/bin/kind https://kind.sigs.k8s.io/dl/${KIND_VERSION}/kind-linux-amd64 \
 && curl -fsSLo /usr/local/bin/kubectl https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl \
 && chmod +x /usr/local/bin/kind /usr/local/bin/kubectl

COPY requirements-runner.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements-runner.txt
