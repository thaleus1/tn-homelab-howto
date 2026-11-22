# 🌐 Enterprise Homelab Architecture Blueprint

**Author:** Ryan Watson ([@thaleus1](https://github.com/thaleus1))  
**Repository:** [tn-homelab-howto](https://github.com/thaleus1/tn-homelab-howto)

This document details the multi-tiered high-availability architecture powering this enterprise-grade homelab blueprint.

---

## 1. High-Level Architecture Diagram

```mermaid
flowchart TD
    subgraph Edge ["🌐 Zero-Trust Edge & Ingress Layer"]
        CF["Cloudflare Edge (WAF, DDoS, Zero Trust)"] -->|Encrypted Tunnel| CFT["Cloudflare Tunnel (cloudflared pods)"]
        CFT --> Traefik["Traefik v3 Ingress Controller (HA 2 Replicas)"]
        Traefik -->|ForwardAuth| Auth["Authentik SSO Portal"]
    end

    subgraph Kubernetes ["☸️ RKE2 Kubernetes Cluster (v1.37.x)"]
        subgraph ControlPlane ["Control Plane (etcd Quorum)"]
            M1["Master 1 (VM 101)"]
            M2["Master 2 (VM 102)"]
            M3["Master 3 (VM 103)"]
            KVIP["kube-vip (API Server VIP: 10.x.x.50)"]
            M1 --- KVIP
            M2 --- KVIP
            M3 --- KVIP
        end

        subgraph StorageTier ["Dedicated Storage Tier (Longhorn v1.12)"]
            L1["Storage 1 (VM 120) - 1.45 TB NVMe"]
            L2["Storage 2 (VM 121) - 1.45 TB NVMe"]
            L3["Storage 3 (VM 122) - 1.45 TB NVMe"]
            L1 <-->|3x Synchronous Replication| L2
            L2 <-->|3x Synchronous Replication| L3
            L3 <-->|3x Synchronous Replication| L1
        end

        subgraph ComputeWorkers ["Compute Workers (General & GPU Workloads)"]
            W1["Worker 1 (VM 131) - 12GB RAM + iGPU"]
            W2["Worker 2 (VM 132) - 12GB RAM + iGPU"]
            W3["Worker 3 (VM 133) - 12GB RAM + iGPU"]
            W4["Worker 4 (VM 111) - 12GB RAM + iGPU"]
            W6["Worker 6 (VM 136) - 12GB RAM + iGPU"]
            W7["Worker 7 (VM 137) - 12GB RAM + iGPU"]
        end
    end

    subgraph Hypervisors ["🖥️ Proxmox VE 9.x Hypervisor Cluster (TNCluster)"]
        PVE1["pve01 (64GB)"]
        PVE2["pve02 (32GB)"]
        PVE3["pve03 (64GB)"]
        PVE4["pve04 (32GB)"]
        PVE5["pve05 (32GB)"]
        PVE6["pve06 (Lenovo M700)"]
        PVE7["pve07 (Lenovo M700)"]
        PVE8["pve08 (32GB)"]
        PVE11["pve11 (Dell OptiPlex)"]
        PVE12["pve12 (Dell OptiPlex)"]
    end

    subgraph External ["🛡️ External Services & Orchestration"]
        NAS["TrueNAS SCALE Storage (NFS Backups + iPXE Netboot)"]
        Cron["Automated Rolling Update Orchestrator (Quorum-Guarded)"]
        NAS --- Cron
    end

    Traefik --> ComputeWorkers
    StorageTier -->|Persistent Volume Claims| ComputeWorkers
    Hypervisors -->|Hosts VMs| Kubernetes
    Cron -->|SSH Rolling Updates| Hypervisors
```

---

## 2. Core Architectural Pillars

### 1. Complete Separation of Storage and Compute
In standard Kubernetes clusters, pods and persistent storage often share nodes. If a worker node crashes or reboots during maintenance, block storage replicas enter a degraded state and begin resynchronizing.
* **Our Solution**: We separate **Dedicated Storage Nodes** (`rke2-longhorn1,3,4`) from **Compute Workers** (`rke2-worker1..7`).
* Storage nodes run Longhorn replication engines on dedicated 1.45 TB NVMe virtual disks. General application workloads are tainted or disabled (`node-role.kubernetes.io/storage=true`).
* Compute worker nodes have storage scheduling **disabled** (`allowScheduling: false`). Worker nodes can reboot, drain, or crash without triggering replica rebuilds.

### 2. High Availability Control Plane with `kube-vip`
* Master nodes run an odd-numbered etcd cluster (3 nodes).
* `kube-vip` provides a floating virtual IP (e.g. `10.90.16.50:6443`) across all masters. Worker nodes and GitOps engines communicate exclusively through the VIP, preventing single-point failure during master maintenance.

### 3. Hypervisor Fleet with Corosync Quorum
* Proxmox VE hosts form an integrated cluster sharing storage (`TNBackups`), network bridges (`vmbr0`), and cluster-wide PCI device mappings.
* Corosync maintains cluster membership. A 10-node cluster requires $\ge 6$ votes for quorum.

### 4. Zero-Trust Edge Security
* Outbound-only Cloudflare Tunnels eliminate open inbound HTTP/S ports on the firewall.
* Traefik terminates traffic with Let's Encrypt wildcard certificates (`*.yourdomain.com`).
* Authentik provides central identity verification (OIDC / OAuth2 / ForwardAuth) with MFA enforcement.

### 5. Automated Hardware & OS Lifecycle
* **Bare-Metal Provisioning**: Unattended network boot via iPXE (`netboot.xyz`) and Proxmox automated answer files (`answer.toml`).
* **Hypervisor Updates**: Automated rolling update orchestrator hosted on TrueNAS, verifying cluster quorum before and after updating each hypervisor.
* **Kubernetes Node Upgrades**: System Upgrade Controller (SUC) for rolling RKE2 upgrades; Kured for guarded OS reboots between 02:00 and 05:00.
