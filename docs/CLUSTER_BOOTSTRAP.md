# 🛠️ Cluster Bootstrapping & Control Plane HA Guide

**Author:** Ryan Watson ([@thaleus1](https://github.com/thaleus1))  
**Repository:** [tn-homelab-howto](https://github.com/thaleus1/tn-homelab-howto)

This guide covers the Day-0 bootstrap procedure to initialize a 3-node high-availability RKE2 control plane using `kube-vip` as a virtual IP, and subsequently join storage and compute worker nodes.

---

## 1. High-Availability Control Plane Design

```
+--------------------------------------------------------------------------+
|                  Virtual IP: 10.90.16.50:6443 (kube-vip)                 |
+--------------------------------------------------------------------------+
       |                                |                               |
       v                                v                               v
+---------------+              +---------------+              +---------------+
| rke2-master1  |              | rke2-master2  |              | rke2-master3  |
| (10.90.16.41) | <----------> | (10.90.16.42) | <----------> | (10.90.16.43) |
| etcd Member 1 |              | etcd Member 2 |              | etcd Member 3 |
+---------------+              +---------------+              +---------------+
```

---

## 2. Step 1: Initialize the First Master (`rke2-master1`)

1. SSH into `rke2-master1`.
2. Create `/etc/rancher/rke2/config.yaml`:
   ```yaml
   token: "<SECURE_BOOTSTRAP_TOKEN>"
   cluster-init: true
   tls-san:
     - "10.90.16.50"
     - "10.90.16.41"
     - "rke2-master1"
     - "kube-api.yourdomain.com"
   cni:
     - canal
   ```

3. Install and start RKE2 server:
   ```bash
   curl -sfL https://get.rke2.io | INSTALL_RKE2_VERSION="v1.37.2+rke2r1" sh -
   systemctl enable --now rke2-server.service
   ```

4. Configure `kubectl` access:
   ```bash
   mkdir -p ~/.kube
   cp /etc/rancher/rke2/rke2.yaml ~/.kube/config
   chmod 600 ~/.kube/config
   ```

---

## 3. Step 2: Deploy `kube-vip` Virtual IP

Once `rke2-master1` is running, deploy `kube-vip` to broadcast the Virtual IP (`10.90.16.50`):

```bash
kubectl apply -f bootstrap/kube-vip.yaml
```

Verify that the VIP is reachable:
```bash
ping -c 3 10.90.16.50
```

---

## 4. Step 3: Join Masters 2 and 3

On `rke2-master2` and `rke2-master3`:

1. Create `/etc/rancher/rke2/config.yaml`:
   ```yaml
   server: "https://10.90.16.50:6443"
   token: "<SECURE_BOOTSTRAP_TOKEN>"
   tls-san:
     - "10.90.16.50"
   cni:
     - canal
   ```

2. Start the service:
   ```bash
   curl -sfL https://get.rke2.io | INSTALL_RKE2_VERSION="v1.37.2+rke2r1" sh -
   systemctl enable --now rke2-server.service
   ```

---

## 5. Step 4: Join Storage & Worker Nodes

On each storage node (`rke2-longhorn*`) and compute worker (`rke2-worker*`):

1. Create `/etc/rancher/rke2/config.yaml`:
   ```yaml
   server: "https://10.90.16.50:6443"
   token: "<SECURE_BOOTSTRAP_TOKEN>"
   ```

2. Start the agent:
   ```bash
   curl -sfL https://get.rke2.io | INSTALL_RKE2_TYPE="agent" INSTALL_RKE2_VERSION="v1.37.2+rke2r1" sh -
   systemctl enable --now rke2-agent.service
   ```

---

## 6. Step 5: Label Nodes for Scheduling Separation

From your management workstation, enforce separation of concerns:

```bash
# Label dedicated storage nodes
kubectl label nodes rke2-longhorn1 rke2-longhorn3 rke2-longhorn4 node-role.kubernetes.io/storage=true

# Label compute workers
kubectl label nodes rke2-worker1 rke2-worker2 rke2-worker3 rke2-worker4 rke2-worker6 rke2-worker7 worker=true

# Label GPU workers for hardware transcoding (Plex, Jellyfin, Tdarr)
kubectl label nodes rke2-worker1 rke2-worker2 rke2-worker3 rke2-worker4 rke2-worker6 rke2-worker7 plex=true
```
