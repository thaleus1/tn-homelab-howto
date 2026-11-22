# 🛠️ Cluster Bootstrapping Manifests

This directory contains configuration templates for initializing the high-availability RKE2 control plane.

* [`kube-vip.yaml`](kube-vip.yaml): Floating Control Plane Virtual IP DaemonSet (`10.90.16.50:6443`).
* [`rke2-server-config.yaml`](rke2-server-config.yaml): Template for `/etc/rancher/rke2/config.yaml` on Master nodes.
* [`rke2-agent-config.yaml`](rke2-agent-config.yaml): Template for `/etc/rancher/rke2/config.yaml` on Worker & Storage nodes.

See the complete step-by-step runbook in [`docs/CLUSTER_BOOTSTRAP.md`](../docs/CLUSTER_BOOTSTRAP.md).
