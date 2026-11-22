#!/usr/bin/env bash
set -euo pipefail

LOG_FILE="/var/log/first-boot.log"
exec > >(tee -a "${LOG_FILE}") 2>&1

echo "========================================================"
echo "Starting Proxmox Automated First-Boot Provisioning"
echo "Timestamp: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "Host: $(hostname -f) ($(hostname -I 2>/dev/null || true))"
echo "========================================================"

LEADER_IP="${LEADER_IP:-172.16.36.53}"
CLUSTER_FINGERPRINT="${CLUSTER_FINGERPRINT:-A3:D7:44:83:04:E8:B0:94:8A:77:85:62:FB:1C:55:A8:F1:CE:DE:34:D3:BA:11:59:8F:CD:C8:CA:87:43:6C:96}"

# 1. Wait for network connectivity to cluster leader
echo "[1/4] Checking connectivity to cluster leader (${LEADER_IP})..."
MAX_RETRIES=30
RETRY_COUNT=0
until ping -c 1 -W 2 "${LEADER_IP}" >/dev/null 2>&1; do
    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ "${RETRY_COUNT}" -ge "${MAX_RETRIES}" ]; then
        echo "ERROR: Cluster leader ${LEADER_IP} unreachable after ${MAX_RETRIES} attempts."
        exit 1
    fi
    echo "Waiting for ${LEADER_IP}... (${RETRY_COUNT}/${MAX_RETRIES})"
    sleep 3
done
echo "Leader ${LEADER_IP} is reachable."

# 2. Check if already in a cluster
echo "[2/4] Verifying cluster membership state..."
if pvecm status >/dev/null 2>&1; then
    echo "Node is already a member of a cluster. Skipping join."
    exit 0
fi

# 3. Join cluster
echo "[3/4] Joining Proxmox cluster via ${LEADER_IP}..."

# Retrieve password from local configuration or environment
CLUSTER_PASSWORD="${CLUSTER_PASSWORD:-}"
if [ -z "${CLUSTER_PASSWORD}" ] && [ -f "/etc/pve-cluster-password" ]; then
    CLUSTER_PASSWORD="$(cat /etc/pve-cluster-password)"
fi

python3 - <<'PYEOF'
import os
import pty
import select
import sys
import time

leader_ip = os.environ.get("LEADER_IP", "172.16.36.53")
fingerprint = os.environ.get("CLUSTER_FINGERPRINT", "")
password = os.environ.get("CLUSTER_PASSWORD", "")

master, slave = pty.openpty()
pid = os.fork()

if pid == 0:
    os.close(master)
    os.dup2(slave, 0)
    os.dup2(slave, 1)
    os.dup2(slave, 2)
    os.close(slave)
    cmd = ['pvecm', 'add', leader_ip, '--use_ssh']
    if fingerprint:
        cmd.extend(['--fingerprint', fingerprint])
    os.execv('/usr/bin/pvecm', cmd)
else:
    os.close(slave)
    buffer = b""
    password_sent = False
    start_time = time.time()
    
    while True:
        r, _, _ = select.select([master], [], [], 5)
        if r:
            try:
                chunk = os.read(master, 1024)
            except OSError:
                break
            if not chunk:
                break
            buffer += chunk
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()

            buf_str = buffer.decode('utf-8', errors='ignore').lower()
            if not password_sent and ("password" in buf_str or "password for" in buf_str):
                time.sleep(0.5)
                os.write(master, (password + "\n").encode('utf-8'))
                password_sent = True
                buffer = b""

        if time.time() - start_time > 180:
            print("\nTimed out waiting for pvecm add to complete.")
            os.kill(pid, 9)
            sys.exit(1)

    _, status = os.waitpid(pid, 0)
    if os.WIFEXITED(status):
        code = os.WEXITSTATUS(status)
        if code != 0:
            print(f"pvecm exited with status {code}")
            sys.exit(code)
    else:
        sys.exit(1)
PYEOF

# 4. Wait for Corosync quorum
echo "[4/4] Verifying cluster quorum..."
for i in {1..30}; do
    if pvecm status >/dev/null 2>&1; then
        echo "Cluster is quorate and operational!"
        pvecm status
        break
    fi
    sleep 2
done

echo "========================================================"
echo "Proxmox Automated First-Boot Completed Successfully!"
echo "Timestamp: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "========================================================"
