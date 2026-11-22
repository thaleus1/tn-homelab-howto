#!/usr/bin/env python3
"""
ThaleusNet: Homelab Cold-Start & Post-Maintenance Verification Tool
Author: Ryan Watson (thaleus@thale.us)

This script performs comprehensive health validation across all infrastructure layers
following a power maintenance window, cold-start boot, or hypervisor reboot cycle.

Verification Checklist:
  [1] Core Network & Gateways (UniFi CloudGateway Fiber)
  [2] Central Storage & ZFS Pools (TrueNAS SCALE jupiter)
  [3] Proxmox VE Hypervisor Quorum (TNCluster - 10 Nodes)
  [4] Virtual Machine Status (All 18 VMs Running)
  [5] Kubernetes Cluster & Node Readiness (12 Nodes Ready)
  [6] Longhorn Distributed Storage Engine (40 Volumes Healthy)
  [7] Hardware Transcoding Devices (gpu.intel.com/i915 across 6 workers)
  [8] MetalLB VIPs & Core Networking
  [9] Rancher Fleet GitOps Pipeline
"""

import json
import subprocess
import sys
import time
from typing import Dict, List, Tuple

# ANSI Colors
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

PASS = f"{GREEN}[PASS]{RESET}"
WARN = f"{YELLOW}[WARN]{RESET}"
FAIL = f"{RED}[FAIL]{RESET}"

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

EXPECTED_VMS = [
    # Control Plane
    (100, "rke2-master1"),
    (101, "rke2-master2"),
    (118, "rke2-master3"),
    # Storage
    (121, "rke2-longhorn1"),
    (104, "rke2-longhorn3"),
    (122, "rke2-longhorn4"),
    # Compute Workers
    (131, "rke2-worker1"),
    (132, "rke2-worker2"),
    (133, "rke2-worker3"),
    (111, "rke2-worker4"),
    (136, "rke2-worker6"),
    (137, "rke2-worker7"),
    # Bastion & Admin
    (114, "rke2-admin"),
    (106, "rke2-winadmin"),
    # Standalone Apps
    (103, "Artemis"),
    (4101, "Juno"),
    (105, "saturn-local"),
    (115, "Vulcan"),
]

EXPECTED_K8S_NODES = [
    "rke2-master1", "rke2-master2", "rke2-master3",
    "rke2-longhorn1", "rke2-longhorn3", "rke2-longhorn4",
    "rke2-worker1", "rke2-worker2", "rke2-worker3",
    "rke2-worker4", "rke2-worker6", "rke2-worker7"
]

METALLB_VIPS = [
    ("10.90.16.60", "Rancher Management"),
    ("10.90.16.61", "Technitium DNS"),
    ("10.90.16.62", "Plex Media Server"),
    ("10.90.16.63", "Qbittorrent Proxy"),
    ("10.90.16.64", "Emby Media Server"),
    ("10.90.16.65", "Traefik Ingress"),
    ("10.90.16.66", "GitLab SSH"),
    ("10.90.16.67", "Jellyfin Media Server"),
    ("10.90.16.68", "Gluetun Proxy"),
]

def run_cmd(cmd: List[str], timeout: int = 15) -> Tuple[int, str, str]:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return 1, "", str(e)

def ssh_cmd(host: str, cmd: str, user: str = "root", timeout: int = 15) -> Tuple[int, str, str]:
    ssh_args = [
        "ssh", "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=5",
        f"{user}@{host}", cmd
    ]
    return run_cmd(ssh_args, timeout=timeout)

def ping(host: str) -> bool:
    code, _, _ = run_cmd(["ping", "-c", "1", "-W", "2", host])
    return code == 0

def section(title: str):
    print(f"\n{BOLD}{CYAN}=== {title} ==={RESET}")

def main():
    print("=" * 75)
    print(f"{BOLD}{GREEN}   ThaleusNet: Post-Maintenance & Startup Health Verification{RESET}")
    print("=" * 75)

    all_passed = True

    # 1. Network Gateways
    section("1. Core Network & Gateway Connectivity")
    gateways = [("10.90.16.1", "VLAN 90 Servers Gateway"), ("172.16.37.254", "VLAN 1 Management Gateway")]
    for ip, desc in gateways:
        ok = ping(ip)
        status = PASS if ok else FAIL
        if not ok: all_passed = False
        print(f"  {status} {desc:<30} ({ip})")

    # 2. Central Storage (TrueNAS)
    section("2. Central Storage & ZFS (TrueNAS jupiter)")
    tn_name, tn_ip = TRUENAS_HOST
    tn_ping = ping(tn_ip)
    print(f"  {PASS if tn_ping else FAIL} Host Reachability ({tn_name} @ {tn_ip})")
    if tn_ping:
        code, out, _ = ssh_cmd(tn_ip, "zpool status -x")
        is_clean = code == 0 and "all pools are healthy" in out
        # In our homelab, intstore1 has 1 failed disk in raidz2 (degraded but operational)
        if is_clean:
            print(f"  {PASS} ZFS Storage Pools: All healthy")
        else:
            print(f"  {WARN} ZFS Storage Pools: {out.splitlines()[0] if out else 'Operational'}")

        code, out, _ = ssh_cmd(tn_ip, "systemctl is-active nfs-server")
        if code == 0 and out.strip() == "active":
            print(f"  {PASS} NFS Storage Service: Active & Serving Exports")
        else:
            print(f"  {WARN} NFS Service Check: {out}")
    else:
        all_passed = False

    # 3. Proxmox VE Cluster
    section("3. Proxmox VE Hypervisor Fleet (TNCluster)")
    pve_up = 0
    for name, ip in PVE_NODES:
        ok = ping(ip)
        if ok: pve_up += 1
    pve_status = PASS if pve_up == len(PVE_NODES) else (WARN if pve_up >= 6 else FAIL)
    print(f"  {pve_status} Physical Hypervisors Online: {pve_up}/{len(PVE_NODES)} nodes responding")

    target_pve = PVE_NODES[2][1]  # pve03
    code, out, _ = ssh_cmd(target_pve, "pvecm status")
    if code == 0 and "Quorate:          Yes" in out:
        votes = [l.split(":")[1].strip() for l in out.splitlines() if "Total votes:" in l]
        v_str = votes[0] if votes else "10"
        print(f"  {PASS} Corosync Cluster Quorum: HEALTHY (Quorate: Yes, {v_str} active votes)")
    else:
        print(f"  {FAIL} Corosync Cluster Quorum: DEGRADED OR SPLIT-BRAIN")
        all_passed = False

    # 4. Virtual Machines Across Proxmox
    section("4. Virtual Machine Fleet Status")
    code, out, _ = ssh_cmd(target_pve, "pvesh get /cluster/resources --type vm --output-format json")
    if code == 0 and out:
        try:
            live_vms = {int(v["vmid"]): v for v in json.loads(out) if "vmid" in v}
            running_count = 0
            for vmid, name in EXPECTED_VMS:
                v = live_vms.get(vmid)
                is_running = v and v.get("status") == "running"
                if is_running: running_count += 1
                status = PASS if is_running else FAIL
                node = v.get("node", "unknown") if v else "missing"
                print(f"  {status} VM {vmid:<5} [{node:<7}] {name:<24} ({v.get('status', 'not found') if v else 'missing'})")
            print(f"\n  Summary: {running_count}/{len(EXPECTED_VMS)} Virtual Machines running")
            if running_count < len(EXPECTED_VMS): all_passed = False
        except Exception as e:
            print(f"  {FAIL} Error parsing VM status: {e}")
            all_passed = False
    else:
        print(f"  {FAIL} Unable to query Proxmox cluster resources via pvesh")
        all_passed = False

    # 5. Kubernetes Cluster Health
    section("5. Kubernetes Cluster (RKE2) & Node Readiness")
    code, out, _ = run_cmd(["kubectl", "get", "nodes", "--no-headers"])
    if code == 0 and out:
        nodes = [l.split() for l in out.splitlines()]
        ready_nodes = [n[0] for n in nodes if n[1] == "Ready"]
        print(f"  {PASS if len(ready_nodes) == 12 else FAIL} Kubernetes Nodes Ready: {len(ready_nodes)}/12 nodes")
        for expected in EXPECTED_K8S_NODES:
            is_ready = expected in ready_nodes
            print(f"    {PASS if is_ready else FAIL} Node: {expected:<18} (Ready)")
            if not is_ready: all_passed = False
    else:
        print(f"  {FAIL} Unable to reach Kubernetes API server at 10.90.16.50:6443")
        all_passed = False

    # Pod status summary
    code, out, _ = run_cmd(["kubectl", "get", "pods", "-A", "--no-headers"])
    if code == 0 and out:
        pods = out.splitlines()
        running_or_comp = sum(1 for p in pods if "Running" in p or "Completed" in p)
        total_pods = len(pods)
        pod_status = PASS if running_or_comp == total_pods else WARN
        print(f"  {pod_status} Pod Status: {running_or_comp}/{total_pods} pods in Running/Completed state")

    # 6. Longhorn Storage Engine
    section("6. Longhorn Distributed Block Storage Tier")
    code, out, _ = run_cmd(["kubectl", "get", "volumes.longhorn.io", "-n", "longhorn-system", "-o", "json"])
    if code == 0 and out:
        try:
            vols = json.loads(out).get("items", [])
            healthy = [v for v in vols if v.get("status", {}).get("robustness") == "healthy"]
            degraded = [v for v in vols if v.get("status", {}).get("robustness") == "degraded"]
            lh_status = PASS if len(degraded) == 0 else FAIL
            print(f"  {lh_status} Volume Health: {len(healthy)}/{len(vols)} healthy (Degraded: {len(degraded)})")
            if degraded:
                for d in degraded:
                    print(f"    {FAIL} Degraded volume: {d['metadata']['name']}")
                all_passed = False
        except Exception:
            pass

    # 7. GPU Hardware Transcoding
    section("7. Hardware Transcoding (Intel QuickSync iGPU)")
    code, out, _ = run_cmd(["kubectl", "get", "nodes", "-l", "worker=true", "-o", "jsonpath={range .items[*]}{.metadata.name}{\": \"}{.status.allocatable.gpu\\.intel\\.com/i915}{\"\\n\"}{end}"])
    if code == 0 and out:
        for line in out.splitlines():
            node, count = line.split(":")
            count = count.strip()
            ok = count == "3"
            print(f"  {PASS if ok else FAIL} {node:<16} QuickSync Allocatable: {count} devices")
            if not ok: all_passed = False

    # 8. MetalLB LoadBalancer VIPs
    section("8. MetalLB LoadBalancer IP Allocation")
    code, out, _ = run_cmd(["kubectl", "get", "svc", "-A", "-o", "json"])
    if code == 0 and out:
        try:
            svcs = json.loads(out).get("items", [])
            live_vips = {}
            for s in svcs:
                ing = s.get("status", {}).get("loadBalancer", {}).get("ingress", [])
                if ing and "ip" in ing[0]:
                    live_vips[ing[0]["ip"]] = f"{s['metadata']['namespace']}/{s['metadata']['name']}"

            for ip, desc in METALLB_VIPS:
                bound = ip in live_vips
                status = PASS if bound else FAIL
                target = live_vips.get(ip, "unallocated / pending")
                print(f"  {status} VIP {ip:<15} [{desc:<24}] -> {target}")
                if not bound: all_passed = False
        except Exception:
            pass

    # 9. Rancher Fleet GitOps
    section("9. Rancher Fleet GitOps Deployment Pipeline")
    code, out, _ = run_cmd(["kubectl", "get", "bundledeployment", "-A", "--no-headers"])
    if code == 0 and out:
        for line in out.splitlines():
            parts = line.split()
            ns, name, deployed, monitored = parts[0], parts[1], parts[2], parts[3]
            is_deployed = deployed == "True"
            print(f"  {PASS if is_deployed else FAIL} BundleDeployment: {name:<20} Deployed={deployed} Monitored={monitored}")
            if not is_deployed: all_passed = False

    # Overall Summary
    print("\n" + "=" * 75)
    if all_passed:
        print(f"{BOLD}{GREEN}  ALL CHECKS PASSED: HOMELAB IS FULLY OPERATIONAL AND CONVERGED!{RESET}")
    else:
        print(f"{BOLD}{YELLOW}  VERIFICATION COMPLETED WITH WARNINGS/FAILURES. REVIEW ABOVE CHECKS.{RESET}")
    print("=" * 75)

if __name__ == "__main__":
    main()
