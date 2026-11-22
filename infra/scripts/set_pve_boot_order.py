#!/usr/bin/env python3
"""
ThaleusNet: Proxmox VE Startup Order Configuration Helper
Author: Ryan Watson (thaleus@thale.us)

This script configures the declarative startup sequence across all 18 active VMs
in the Proxmox VE cluster (TNCluster). It sets startup order and up delays so that
when physical hypervisors power on, the virtual infrastructure initializes in strict
dependency order:
  Order 1 (up 60s): RKE2 Control Plane Masters (etcd quorum & kube-vip)
  Order 2 (up 60s): RKE2 Storage Nodes (Longhorn 3x replica storage)
  Order 3 (up 30s): RKE2 Compute Workers (Workloads & Intel iGPU transcoding)
  Order 4 (up 30s): Administration & Bastion VMs (rke2-admin, rke2-winadmin)
  Order 5 (up  0s): Standalone Application VMs (Artemis, Juno, Vulcan, Saturn)
"""

import json
import subprocess
import sys

# Target startup configuration: vmid -> (order, up_delay, description)
STARTUP_CONFIG = {
    # Tier 1: Control Plane Masters (etcd Quorum & API VIP)
    100: (1, 60, "rke2-master1"),
    101: (1, 60, "rke2-master2"),
    118: (1, 60, "rke2-master3"),

    # Tier 2: Storage Nodes (Longhorn Replicated NVMe)
    121: (2, 60, "rke2-longhorn1"),
    104: (2, 60, "rke2-longhorn3"),
    122: (2, 60, "rke2-longhorn4"),

    # Tier 3: Compute Workers (Workloads & iGPU QuickSync)
    131: (3, 30, "rke2-worker1"),
    132: (3, 30, "rke2-worker2"),
    133: (3, 30, "rke2-worker3"),
    111: (3, 30, "rke2-worker4"),
    136: (3, 30, "rke2-worker6"),
    137: (3, 30, "rke2-worker7"),

    # Tier 4: Management & Bastion VMs
    114: (4, 30, "rke2-admin"),
    106: (4, 30, "rke2-winadmin"),

    # Tier 5: Standalone Application VMs
    103: (5, 0, "Artemis (Plex Docker)"),
    4101: (5, 0, "Juno"),
    105: (5, 0, "saturn-local"),
    115: (5, 0, "Vulcan"),
}

PROXMOX_HOST = "172.16.36.53"  # pve03 (or any reachable cluster node)

def run_remote(cmd: str) -> str:
    full_cmd = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", f"root@{PROXMOX_HOST}", cmd]
    res = subprocess.run(full_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Command failed ({res.returncode}): {res.stderr.strip()}")
    return res.stdout.strip()

def main():
    print("=" * 70)
    print("  ThaleusNet: Configuring Proxmox VM Startup Orders")
    print("=" * 70)

    # 1. Fetch live cluster VM locations
    print("Fetching live cluster resources from Proxmox...")
    raw_vms = run_remote("pvesh get /cluster/resources --type vm --output-format json")
    vm_list = json.loads(raw_vms)
    vm_node_map = {int(v["vmid"]): v["node"] for v in vm_list if "vmid" in v and "node" in v}

    # 2. Apply startup order to all configured VMs
    for vmid, (order, up, desc) in sorted(STARTUP_CONFIG.items(), key=lambda x: (x[1][0], x[0])):
        node = vm_node_map.get(vmid)
        if not node:
            print(f"Skipping VM {vmid} ({desc}) - not found in cluster inventory")
            continue

        startup_val = f"order={order},up={up}"
        print(f"Setting VM {vmid:<5} on {node:<6} [{desc:<24}] -> startup: {startup_val} ... ", end="", flush=True)
        try:
            run_remote(f"pvesh set /nodes/{node}/qemu/{vmid}/config --onboot 1 --startup {startup_val}")
            print("OK")
        except Exception as e:
            print(f"FAILED: {e}")

    # 3. Print verification table
    print("\nVerifying updated startup configurations across cluster:")
    print("-" * 70)
    print(f"{'VMID':<6} {'Name':<22} {'Node':<8} {'OnBoot':<8} {'Startup Order'}")
    print("-" * 70)
    output = run_remote("""python3 -c '
import os, glob
conf_files = glob.glob("/etc/pve/nodes/*/qemu-server/*.conf")
for f in sorted(conf_files):
    vmid = os.path.basename(f).replace(".conf", "")
    node = f.split("/")[4]
    with open(f) as fh:
        c = fh.read()
    name = next((l.split(":")[1].strip() for l in c.splitlines() if l.startswith("name:")), "")
    onboot = next((l.split(":")[1].strip() for l in c.splitlines() if l.startswith("onboot:")), "0")
    startup = next((l.split(":")[1].strip() for l in c.splitlines() if l.startswith("startup:")), "none")
    if onboot == "1":
        print(f"{vmid:<6} {name:<22} {node:<8} {onboot:<8} {startup}")
'""")
    print(output)
    print("=" * 70)
    print("Startup sequencing successfully configured on Proxmox VE.")

if __name__ == "__main__":
    main()
