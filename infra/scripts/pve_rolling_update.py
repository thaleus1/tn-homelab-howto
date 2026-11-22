#!/usr/bin/env python3
"""
Proxmox VE Cluster Rolling Update Orchestrator
Executes safe, sequential rolling updates across all Proxmox nodes from an external host.
Maintains Corosync cluster quorum, checks kernel mismatch reboot requirements, and prevents split-brain.
"""

import argparse
import datetime
import logging
import os
import subprocess
import sys
import time

DEFAULT_NODES = [
    "pve01", "pve02", "pve03", "pve04", "pve05",
    "pve06", "pve07", "pve08", "pve11", "pve12"
]
DEFAULT_DOMAIN = os.environ.get("PVE_CLUSTER_DOMAIN", "yourdomain.com")
DEFAULT_LOG_DIR = os.environ.get("PVE_LOG_DIR", "/var/log/pve-rolling-update")


def setup_logger(log_dir: str) -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"update_{ts}.log")

    logger = logging.getLogger("pve_updater")
    logger.setLevel(logging.INFO)

    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.INFO)
    file_format = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
    file_handler.setFormatter(file_format)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter("[%(asctime)s] %(message)s", "%H:%M:%S")
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

    logger.info(f"Logging initialized -> {log_file}")
    return logger


def ssh_exec(host: str, cmd: str, timeout: int = 300) -> subprocess.CompletedProcess:
    ssh_cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        f"root@{host}",
        cmd
    ]
    return subprocess.run(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)


def check_cluster_quorum(nodes: list, domain: str, logger: logging.Logger) -> bool:
    """Queries cluster status from any reachable node to verify quorum."""
    for node in nodes:
        host = f"{node}.{domain}" if domain else node
        try:
            res = ssh_exec(host, "pvecm status", timeout=15)
            if res.returncode == 0:
                if "Quorate:          Yes" in res.stdout or "Flags:            Quorate" in res.stdout:
                    logger.info(f"Quorum verified via {node}: Cluster is quorate.")
                    return True
                else:
                    logger.warning(f"Node {node} reported cluster is NOT quorate!")
                    return False
        except Exception:
            continue
    logger.error("Failed to query quorum from any reachable cluster node!")
    return False


def get_upgradable_packages(node: str, domain: str, logger: logging.Logger) -> list:
    host = f"{node}.{domain}" if domain else node
    try:
        ssh_exec(host, "apt-get update -qq", timeout=90)
        res = ssh_exec(host, "apt list --upgradable 2>/dev/null", timeout=30)
        if res.returncode != 0:
            return []
        lines = [line.strip() for line in res.stdout.splitlines() if line.strip() and not line.startswith("Listing")]
        return lines
    except Exception as e:
        logger.error(f"Error checking upgradable packages on {node}: {e}")
        return []


def is_reboot_required(node: str, domain: str, logger: logging.Logger) -> bool:
    host = f"{node}.{domain}" if domain else node
    try:
        # Check standard reboot-required flag
        res = ssh_exec(host, "[ -f /var/run/reboot-required ] && echo yes || echo no", timeout=10)
        if res.stdout.strip() == "yes":
            return True

        # Check if running kernel is older than latest installed kernel
        res_running = ssh_exec(host, "uname -r", timeout=10)
        res_installed = ssh_exec(host, "ls -1 /boot/vmlinuz-* 2>/dev/null | sort -V | tail -n 1", timeout=10)
        if res_running.returncode == 0 and res_installed.returncode == 0:
            running_k = res_running.stdout.strip()
            latest_k = os.path.basename(res_installed.stdout.strip()).replace("vmlinuz-", "")
            if running_k and latest_k and running_k != latest_k:
                logger.info(f"{node}: Running kernel '{running_k}' differs from newest installed '{latest_k}'.")
                return True
    except Exception as e:
        logger.error(f"Error checking reboot requirement on {node}: {e}")
    return False


def wait_for_node_reboot(node: str, domain: str, logger: logging.Logger, max_wait: int = 360, settle_time: int = 45) -> bool:
    host = f"{node}.{domain}" if domain else node
    logger.info(f"Waiting for {node} to go offline...")
    start_time = time.time()

    while time.time() - start_time < 90:
        res = ssh_exec(host, "echo ping", timeout=5)
        if res.returncode != 0:
            logger.info(f"{node} is down.")
            break
        time.sleep(2)

    logger.info(f"Waiting for {node} to boot back up and respond to SSH...")
    boot_start = time.time()
    while time.time() - boot_start < max_wait:
        res = ssh_exec(host, "uptime", timeout=5)
        if res.returncode == 0:
            logger.info(f"{node} is back online! ({res.stdout.strip()})")
            logger.info(f"Waiting {settle_time}s settle time for cluster and VMs to stabilize...")
            time.sleep(settle_time)
            return True
        time.sleep(5)

    logger.error(f"Timed out waiting for {node} to come back online after {max_wait}s!")
    return False


def process_node(node: str, domain: str, args, logger: logging.Logger) -> dict:
    host = f"{node}.{domain}" if domain else node
    logger.info("=" * 60)
    logger.info(f"Processing node: {node} ({host})")
    logger.info("=" * 60)

    result = {
        "node": node,
        "upgraded": 0,
        "rebooted": False,
        "status": "OK"
    }

    upgradable = get_upgradable_packages(node, domain, logger)
    logger.info(f"{node}: {len(upgradable)} package(s) pending update.")
    for pkg in upgradable[:10]:
        logger.info(f"  - {pkg}")
    if len(upgradable) > 10:
        logger.info(f"  ... and {len(upgradable) - 10} more.")

    result["upgraded"] = len(upgradable)

    if args.dry_run:
        reboot_needed = is_reboot_required(node, domain, logger)
        logger.info(f"[DRY-RUN] {node}: Would upgrade {len(upgradable)} packages. Reboot needed: {reboot_needed}")
        return result

    if len(upgradable) > 0:
        logger.info(f"{node}: Applying dist-upgrade...")
        res = ssh_exec(host, "DEBIAN_FRONTEND=noninteractive apt-get dist-upgrade -y", timeout=900)
        if res.returncode != 0:
            logger.error(f"{node}: Upgrade failed with return code {res.returncode}:\n{res.stderr}")
            result["status"] = "UPGRADE_FAILED"
            return result
        logger.info(f"{node}: Upgrade applied successfully.")

        # Clean old packages/kernels
        ssh_exec(host, "DEBIAN_FRONTEND=noninteractive apt-get --purge autoremove -y", timeout=300)

    # Check if reboot is needed
    if not args.no_reboot and is_reboot_required(node, domain, logger):
        logger.info(f"{node}: Reboot required. Initiating reboot...")
        ssh_exec(host, "systemctl reboot", timeout=10)
        reboot_ok = wait_for_node_reboot(node, domain, logger, settle_time=args.settle_seconds)
        if not reboot_ok:
            result["status"] = "REBOOT_FAILED"
            return result
        result["rebooted"] = True
    else:
        logger.info(f"{node}: No reboot required.")

    return result


def main():
    parser = argparse.ArgumentParser(description="Proxmox VE Cluster Rolling Update Orchestrator")
    parser.add_argument("--dry-run", action="store_true", help="Perform checks only without applying updates or rebooting")
    parser.add_argument("--nodes", type=str, default="", help="Comma-separated list of nodes to target (e.g. pve01,pve02)")
    parser.add_argument("--domain", type=str, default=DEFAULT_DOMAIN, help="Cluster domain suffix (e.g. yourdomain.com)")
    parser.add_argument("--log-dir", type=str, default=DEFAULT_LOG_DIR, help="Directory to store run logs")
    parser.add_argument("--no-reboot", action="store_true", help="Do not reboot nodes even if a reboot is required")
    parser.add_argument("--settle-seconds", type=int, default=45, help="Seconds to wait after a rebooted node returns (default: 45)")
    args = parser.parse_args()

    logger = setup_logger(args.log_dir)
    target_nodes = [n.strip() for n in args.nodes.split(",") if n.strip()] if args.nodes else DEFAULT_NODES

    logger.info("Starting Proxmox Cluster Rolling Update Run")
    logger.info(f"Target Nodes: {', '.join(target_nodes)}")
    logger.info(f"Domain: {args.domain}")
    logger.info(f"Dry Run Mode: {args.dry_run}")
    logger.info(f"Automatic Reboot: {not args.no_reboot}")

    # 1. Pre-flight Quorum Check
    logger.info("Executing Pre-Flight Cluster Quorum Check...")
    if not check_cluster_quorum(target_nodes, args.domain, logger):
        logger.critical("Pre-flight quorum check FAILED. Aborting rolling updates to prevent cluster degradation.")
        sys.exit(1)
    logger.info("Pre-flight check PASSED.")

    # 2. Sequential Rolling Execution
    summary = []
    for node in target_nodes:
        if not check_cluster_quorum(target_nodes, args.domain, logger):
            logger.critical(f"Cluster lost quorum before processing {node}! Halting rolling updates immediately.")
            break

        res = process_node(node, args.domain, args, logger)
        summary.append(res)

        if res["status"] != "OK":
            logger.critical(f"Node {node} finished with status '{res['status']}'. Halting sequence to protect cluster.")
            break

    # 3. Final Summary Report
    logger.info("=" * 60)
    logger.info("PROXMOX ROLLING UPDATE SUMMARY REPORT")
    logger.info("=" * 60)
    logger.info(f"{'Node':<10} {'Packages Upgraded':<20} {'Rebooted':<12} {'Status':<15}")
    logger.info("-" * 60)
    for s in summary:
        logger.info(f"{s['node']:<10} {s['upgraded']:<20} {str(s['rebooted']):<12} {s['status']:<15}")
    logger.info("=" * 60)

    final_quorum = check_cluster_quorum(target_nodes, args.domain, logger)
    logger.info(f"Final Cluster Quorum Status: {'QUORATE (HEALTHY)' if final_quorum else 'DEGRADED'}")
    logger.info("Rolling update run completed.")


if __name__ == "__main__":
    main()
