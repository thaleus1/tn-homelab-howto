# 🏗️ OpenTofu Infrastructure as Code (Proxmox VE)

**Author:** Ryan Watson ([@thaleus1](https://github.com/thaleus1))  
**Repository:** [tn-homelab-howto](https://github.com/thaleus1/tn-homelab-howto)

This directory contains the declarative **OpenTofu** (Terraform-compatible) automation suite to provision a complete 12-node virtual machine topology across a Proxmox VE hypervisor cluster.

---

## 🚀 Topology Overview

* **3 Control-Plane Master VMs** (`rke2-master1..3`): 4 vCPUs, 8 GB RAM, 50 GB OS disk. Distributed across separate hypervisors for etcd quorum.
* **3 Dedicated Longhorn Storage VMs** (`rke2-longhorn1..3`): 4 vCPUs, 16 GB RAM, 50 GB OS disk + **1.45 TB dedicated NVMe virtual disk** for synchronous 3x replica storage.
* **6 Compute Worker VMs** (`rke2-worker1..6`): 4 vCPUs, 12 GB RAM, 50 GB OS disk, with optional Intel QuickSync iGPU PCIe pass-through.

---

## 🛠️ Usage Instructions

### 1. Prerequisites
* Install OpenTofu (`brew install opentofu` or via package manager).
* Create a Proxmox API token:
  ```bash
  pveum user token add root@pam tofu-token --privsep 0
  ```
* Upload the official Ubuntu 24.04 LTS cloud image to your Proxmox ISO datastore:
  ```bash
  wget https://cloud-images.ubuntu.com/minimal/releases/noble/release/ubuntu-24.04-minimal-cloudimg-amd64.img
  ```

### 2. Configure Variables
```bash
cp terraform.tfvars.example terraform.tfvars
```
Edit `terraform.tfvars` with your API token, SSH public key, node IPs, and storage pool names.

### 3. Deploy
```bash
tofu init
tofu plan
tofu apply
```
Within ~3 minutes, all 12 VMs are created, configured via Cloud-Init, attached to your VLAN bridge, and booted.
