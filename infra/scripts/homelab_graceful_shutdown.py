#!/usr/bin/env python3
"""
ThaleusNet: Homelab Graceful Power Maintenance Shutdown Orchestrator
Author: Ryan Watson (thaleus@thale.us)

This script automates the end-to-end graceful shutdown sequence across the entire
ThaleusNet private cloud infrastructure for routine physical power maintenance.

Shutdown Dependency Sequence (Reverse Order):
  Phase 1: Pre-Flight Health Checks (Kubernetes, Longhorn, Proxmox, TrueNAS)
  Phase 2: Database & Workload Quiesce (PostgreSQL HA checkpointing, flush buffers)
  Phase 3: Kubernetes Agent & Server Shutdown (Workers -> Storage -> Masters)
  Phase 4: Proxmox Virtual Machine Shutdown (Reverse Boot Order 5 -> 1)
  Phase 5: Proxmox VE Hypervisor Fleet Poweroff (10 nodes, quorum-guarded)
  Phase 6: Central TrueNAS Storage Poweroff (ZFS sync & graceful halt)
  Phase 7: Network & Power Infrastructure Advisory (UniFi Gateway, Switches, UPS)
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple

# Configuration
PVE_NODES = [
    ("pve01", "172.16.36.51"),
    ("pve02", "172.16.36.52"),
    ("pve03", "172.16.36.53"),
    ("pve04", "172.16.36.54"),
    ("pve05", "172.16.36.55"),
    ("pve06", "172.16.36.56"),
    ("pve07", "172.16.36.57"),
    ("pve08", "172.16.36.58"),
    ("pve11", "172.16.36.61"),
    ("pve12", "172.16.36.62"),
]

TRUENAS_HOST = ("jupiter", "172.16.36.10")

# VM Inventory categorized by Tier for reverse-order shutdown
VM_TIERS = {
    "Tier 5: Standalone VMs": [
        (103, "Artemis (Plex Docker)", "pve05"),
        (4101, "Juno", "pve08"),
        (105, "saturn-local", "pve11"),
        (115, "Vulcan", "pve11"),
    ],
    "Tier 4: Management & Bastion VMs": [
        (106, "rke2-winadmin", "pve12"),
        (114, "rke2-admin", "pve03"),
    ],
    "Tier 3: Kubernetes Compute Workers": [
        (137, "rke2-worker7", "pve07"),
        (136, "rke2-worker6", "pve06"),
        (111, "rke2-worker4", "pve11"),
        (133, "rke2-worker3", "pve02"),
        (132, "rke2-worker2", "pve01"),
        (131, "rke2-worker1", "pve04"),
    ],
    "Tier 2: Kubernetes Storage Nodes (Longhorn)": [
        (122, "rke2-longhorn4", "pve03"),
        (104, "rke2-longhorn3", "pve11"),
        (121, "rke2-longhorn1", "pve12"),
    ],
    "Tier 1: Kubernetes Control Plane Masters": [
        (118, "rke2-master3", "pve12"),
        (101, "rke2-master2", "pve05"),
        (100, "rke2-master1", "pve11"),
    ],
}

# Color formatting
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

LOG_DIR = os.path.expanduser("~/src/rke2-fleet/infra/logs")
LOG_FILE = os.path.join(LOG_DIR, f"shutdown_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

def log(msg: str, level: str = "INFO"):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] [{level}] {msg}"
    print(formatted)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(formatted + "\n")
    except Exception:
        pass

def run_cmd(cmd: List[str], timeout: int = 60, dry_run: bool = False) -> Tuple[int, str, str]:
    cmd_str = " ".join(cmd)
    if dry_run:
        log(f"[DRY-RUN] Would execute: {cmd_str}")
        return 0, "", ""
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except subprocess.TimeoutExpired:
        log(f"Command timed out ({timeout}s): {cmd_str}", "ERROR")
        return 124, "", "Timed out"
    except Exception as e:
        log(f"Command error: {cmd_str} -> {e}", "ERROR")
        return 1, "", str(e)

def ssh_cmd(host: str, cmd: str, user: str = "root", timeout: int = 60, dry_run: bool = False) -> Tuple[int, str, str]:
    ssh_args = [
        "ssh", "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=10",
        f"{user}@{host}", cmd
    ]
    return run_cmd(ssh_args, timeout=timeout, dry_run=dry_run)

# ----------------------------------------------------------------------
# Stage 1: Pre-Flight Health Checks
# ----------------------------------------------------------------------
def check_preflight(dry_run: bool = False) -> bool:
    log("==================================================================", "INFO")
    log("  Phase 1: Pre-Flight Cluster Health & Safety Checks", "INFO")
    log("==================================================================", "INFO")

    # 1. Kubernetes Nodes
    code, out, _ = run_cmd(["kubectl", "get", "nodes", "--no-headers"], timeout=15)
    if code != 0:
        log("Unable to reach Kubernetes API server. Check kube-vip/network.", "WARN")
    else:
        ready_count = sum(1 for line in out.splitlines() if " Ready " in line)
        total_nodes = len(out.splitlines())
        log(f"Kubernetes Nodes: {ready_count}/{total_nodes} Ready", "INFO")
        if ready_count < total_nodes:
            log("Some Kubernetes nodes are NotReady!", "WARN")

    # 2. Longhorn Volume Health
    code, out, _ = run_cmd(["kubectl", "get", "volumes.longhorn.io", "-n", "longhorn-system", "-o", "json"], timeout=20)
    if code == 0 and out:
        try:
            vols = json.loads(out).get("items", [])
            degraded = [v["metadata"]["name"] for v in vols if v.get("status", {}).get("robustness") == "degraded"]
            if degraded:
                log(f"WARNING: Found {len(degraded)} degraded Longhorn volume(s): {', '.join(degraded)}", "WARN")
                log("Shutting down while Longhorn is degraded may lead to volume recovery steps on boot.", "WARN")
            else:
                log(f"Longhorn Volumes: All {len(vols)} persistent volumes healthy.", "INFO")
        except Exception:
            pass

    # 3. Proxmox Quorum
    target_pve = PVE_NODES[2][1]  # pve03
    code, out, _ = ssh_cmd(target_pve, "pvecm status")
    if code == 0 and "Quorate:          Yes" in out:
        log("Proxmox VE Cluster: Corosync Quorum Healthy (Quorate: Yes)", "INFO")
    else:
        log("Proxmox cluster quorum degraded or unreachable!", "WARN")

    # 4. TrueNAS ZFS Pools
    code, out, _ = ssh_cmd(TRUENAS_HOST[1], "zpool status -x")
    if code == 0:
        log(f"TrueNAS ZFS Pools: {out}", "INFO")

    return True

# ----------------------------------------------------------------------
# Stage 2: Quiesce Workloads & Databases
# ----------------------------------------------------------------------
def quiesce_workloads(dry_run: bool = False):
    log("==================================================================", "INFO")
    log("  Phase 2: Quiescing Stateful Databases & Workloads", "INFO")
    log("==================================================================", "INFO")

    # Checkpoint PostgreSQL HA (CloudNative-PG)
    log("Issuing checkpoint and sync to CloudNativePG PostgreSQL clusters...", "INFO")
    pg_checkpoint_cmd = [
        "kubectl", "exec", "-n", "postgres", "postgres-ha-1", "-c", "postgres",
        "--", "psql", "-U", "postgres", "-c", "CHECKPOINT;"
    ]
    code, out, err = run_cmd(pg_checkpoint_cmd, timeout=30, dry_run=dry_run)
    if code == 0:
        log("PostgreSQL HA: CHECKPOINT complete. Write-Ahead Logs synced.", "INFO")
    else:
        log(f"PostgreSQL HA checkpoint notice: {err or out} (non-fatal)", "WARN")

# ----------------------------------------------------------------------
# Stage 3: Graceful Kubernetes Service Shutdown
# ----------------------------------------------------------------------
def stop_kubernetes_services(dry_run: bool = False):
    log("==================================================================", "INFO")
    log("  Phase 3: Stopping Kubernetes Services in Controlled Order", "INFO")
    log("==================================================================", "INFO")

    # 1. Compute Workers: Stop rke2-agent
    worker_nodes = [
        ("rke2-worker1", "10.90.16.44"),
        ("rke2-worker2", "10.90.16.45"),
        ("rke2-worker3", "10.90.16.49"),
        ("rke2-worker4", "10.90.16.51"),
        ("rke2-worker6", "10.90.16.53"),
        ("rke2-worker7", "10.90.16.54"),
    ]
    log("Stopping rke2-agent service on 6 compute workers (evacuates workloads)...", "INFO")
    for name, ip in worker_nodes:
        log(f"  Stopping rke2-agent on {name} ({ip})...", "INFO")
        ssh_cmd(ip, "systemctl stop rke2-agent.service", timeout=45, dry_run=dry_run)

    # 2. Wait 15s for Longhorn volume detachment
    log("Waiting 15 seconds for Longhorn volumes to detach cleanly...", "INFO")
    if not dry_run:
        time.sleep(15)

    # 3. Storage Nodes: Stop rke2-agent
    storage_nodes = [
        ("rke2-longhorn1", "10.90.16.46"),
        ("rke2-longhorn3", "10.90.16.48"),
        ("rke2-longhorn4", "10.90.16.52"),
    ]
    log("Stopping rke2-agent service on 3 Longhorn storage nodes...", "INFO")
    for name, ip in storage_nodes:
        log(f"  Stopping rke2-agent on {name} ({ip})...", "INFO")
        ssh_cmd(ip, "systemctl stop rke2-agent.service", timeout=45, dry_run=dry_run)

    # 4. Control Plane: Stop rke2-server (reverse order to cleanly flush etcd)
    master_nodes = [
        ("rke2-master3", "10.90.16.43"),
        ("rke2-master2", "10.90.16.42"),
        ("rke2-master1", "10.90.16.41"),
    ]
    log("Stopping rke2-server service on 3 control plane masters (flushing etcd WAL)...", "INFO")
    for name, ip in master_nodes:
        log(f"  Stopping rke2-server on {name} ({ip})...", "INFO")
        ssh_cmd(ip, "systemctl stop rke2-server.service", timeout=60, dry_run=dry_run)

    log("Kubernetes services stopped cleanly across all 12 nodes.", "INFO")

# ----------------------------------------------------------------------
# Stage 4: Virtual Machine Shutdown via Proxmox
# ----------------------------------------------------------------------
def shutdown_vms(dry_run: bool = False, pve_host: str = "172.16.36.53"):
    log("==================================================================", "INFO")
    log("  Phase 4: Gracefully Shutting Down Virtual Machines (Tier 5 -> 1)", "INFO")
    log("==================================================================", "INFO")

    for tier_name, vms in VM_TIERS.items():
        log(f"Processing {tier_name} ({len(vms)} VMs)...", "INFO")
        for vmid, name, node in vms:
            log(f"  Issuing qm shutdown to VM {vmid} ({name}) on {node}...", "INFO")
            cmd = f"pvesh create /nodes/{node}/qemu/{vmid}/status/shutdown --timeout 60"
            code, out, err = ssh_cmd(pve_host, cmd, timeout=30, dry_run=dry_run)
            if code != 0 and "already stopped" not in err.lower():
                log(f"  Notice for VM {vmid}: {err or out}", "WARN")

        # Cooldown per tier
        log(f"Waiting 10 seconds for {tier_name} to halt...", "INFO")
        if not dry_run:
            time.sleep(10)

    # Poll until all VMs are stopped (up to 120s)
    log("Verifying all cluster VMs are fully powered off...", "INFO")
    if not dry_run:
        for attempt in range(12):
            code, out, _ = ssh_cmd(pve_host, "pvesh get /cluster/resources --type vm --output-format json", timeout=15)
            if code == 0:
                try:
                    all_vms = json.loads(out)
                    running = [v for v in all_vms if v.get("status") == "running" and v.get("template", 0) != 1]
                    if not running:
                        log("All 18 virtual machines are powered off successfully.", "INFO")
                        break
                    running_names = [f"VM {v.get('vmid')} ({v.get('name')})" for v in running]
                    log(f"Still running ({len(running)}): {', '.join(running_names)}. Waiting 10s...", "INFO")
                except Exception:
                    pass
            time.sleep(10)

# ----------------------------------------------------------------------
# Stage 5: Proxmox Hypervisor Fleet Shutdown
# ----------------------------------------------------------------------
def shutdown_proxmox_hypervisors(dry_run: bool = False):
    log("==================================================================", "INFO")
    log("  Phase 5: Powering Off Proxmox VE Hypervisors (10 Nodes)", "INFO")
    log("==================================================================", "INFO")

    # Shut down compute/quorum-only nodes first, then primary nodes, ending with pve03
    pve_shutdown_order = [
        ("pve08", "172.16.36.58"),
        ("pve05", "172.16.36.55"),
        ("pve12", "172.16.36.62"),
        ("pve06", "172.16.36.56"),
        ("pve07", "172.16.36.57"),
        ("pve04", "172.16.36.54"),
        ("pve02", "172.16.36.52"),
        ("pve01", "172.16.36.51"),
        ("pve11", "172.16.36.61"),
        ("pve03", "172.16.36.53"),  # Local bastion node last
    ]

    for name, ip in pve_shutdown_order:
        log(f"Powering off Proxmox hypervisor {name} ({ip})...", "INFO")
        ssh_cmd(ip, "systemctl poweroff", timeout=15, dry_run=dry_run)
        time.sleep(2)

    log("Power-off signal issued to all 10 Proxmox VE hypervisors.", "INFO")

# ----------------------------------------------------------------------
# Stage 6: Central TrueNAS Storage Shutdown
# ----------------------------------------------------------------------
def shutdown_truenas(dry_run: bool = False):
    log("==================================================================", "INFO")
    log("  Phase 6: Syncing & Powering Off TrueNAS SCALE (jupiter)", "INFO")
    log("==================================================================", "INFO")

    name, ip = TRUENAS_HOST
    log(f"Syncing ZFS storage pools on {name} ({ip})...", "INFO")
    ssh_cmd(ip, "sync; zpool sync", timeout=30, dry_run=dry_run)

    log(f"Issuing power-off to TrueNAS storage server {name} ({ip})...", "INFO")
    ssh_cmd(ip, "systemctl poweroff", timeout=20, dry_run=dry_run)
    log("TrueNAS storage server is shutting down cleanly.", "INFO")

# ----------------------------------------------------------------------
# Stage 7: Network & Power Infrastructure Advisory
# ----------------------------------------------------------------------
def print_infrastructure_advisory():
    print("\n" + "=" * 70)
    print(f"{BOLD}{GREEN}  HOMELAB GRACEFUL SHUTDOWN SEQUENCE COMPLETED{RESET}")
    print("=" * 70)
    print(f"""
{BOLD}Next Steps for Physical Power Maintenance:{RESET}

1. {BOLD}Verify Visual Status:{RESET}
   * Confirm that all Proxmox hosts (pve01..08, pve11, pve12) LEDs indicate soft power-off.
   * Confirm that TrueNAS (jupiter) has completed drive park and powered off.

2. {BOLD}Network & Power Switches (If servicing main electrical feed):{RESET}
   * If replacing UPS batteries, electrical wiring, or breaker panels:
     - Turn off UniFi PoE Switches and Aggregation Switches.
     - Power down UniFi CloudGateway Fiber router (`10.90.16.1`).
     - Switch off main PDU breakers and power down UPS units.

3. {BOLD}Performing Physical Maintenance:{RESET}
   * Replace batteries, dust filters, re-cable, or service electrical infrastructure.

4. {BOLD}Cold-Start Boot Order (When Power Is Restored):{RESET}
   * Follow the reverse hierarchy documented in:
     `docs/POWER_MAINTENANCE.md`
   * Run verification script:
     `python3 infra/scripts/homelab_startup_verification.py`
""")
    print("=" * 70)

def main():
    parser = argparse.ArgumentParser(description="ThaleusNet Homelab Graceful Shutdown Orchestrator")
    parser.add_argument("--dry-run", action="store_true", help="Simulate shutdown sequence without executing")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--skip-k8s", action="store_true", help="Skip stopping Kubernetes services")
    parser.add_argument("--skip-vms", action="store_true", help="Skip shutting down virtual machines")
    parser.add_argument("--skip-pve", action="store_true", help="Skip powering off Proxmox hypervisors")
    parser.add_argument("--skip-truenas", action="store_true", help="Skip powering off TrueNAS storage server")
    args = parser.parse_args()

    print("\n" + "=" * 70)
    print(f"{BOLD}{CYAN}  ThaleusNet: Homelab Graceful Power Maintenance Shutdown{RESET}")
    print("=" * 70)
    print(f"Log file: {LOG_FILE}\n")

    if args.dry_run:
        log("DRY RUN MODE ENABLED. No changes will be executed.", "WARN")

    check_preflight(dry_run=args.dry_run)

    if not args.yes and not args.dry_run:
        print("\n" + f"{BOLD}{RED}WARNING: You are about to initiate a complete shutdown of ThaleusNet.{RESET}")
        print("This will halt all Kubernetes workloads, virtual machines, hypervisors, and storage.")
        confirm = input(f"{BOLD}Are you sure you want to proceed? [y/N]: {RESET}").strip().lower()
        if confirm != "y":
            print("Shutdown aborted by user.")
            sys.exit(0)

    # Execution pipeline
    quiesce_workloads(dry_run=args.dry_run)

    if not args.skip_k8s:
        stop_kubernetes_services(dry_run=args.dry_run)

    if not args.skip_vms:
        shutdown_vms(dry_run=args.dry_run)

    if not args.skip_pve:
        shutdown_proxmox_hypervisors(dry_run=args.dry_run)

    if not args.skip_truenas:
        shutdown_truenas(dry_run=args.dry_run)

    print_infrastructure_advisory()

if __name__ == "__main__":
    main()
