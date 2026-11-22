# Automated Proxmox Bare-Metal Provisioning Pipeline

This directory contains the automation templates used by the TrueNAS network boot infrastructure (`netboot.xyz`) to automatically install Proxmox VE onto bare-metal hosts unattended.

---

## File Manifest

* [`answer.toml.example`](answer.toml.example): Automated installer answer file configured for SATA/SAS target drives (`sda`).
* [`answer-nvme.toml.example`](answer-nvme.toml.example): Automated installer answer file configured for NVMe target drives (`nvme0n1`).
* [`first-boot.sh`](first-boot.sh): Post-installation bootstrap script that joins the host to the Proxmox cluster via `pvecm`.
* [`custom.ipxe`](custom.ipxe): `netboot.xyz` custom menu deployed to `/mnt/your-pool/netboot-config/menus/custom.ipxe` on TrueNAS.

---

## Operational Guide

1. **Answer File Preparation**: Copy `answer.toml.example` to your TrueNAS HTTP asset directory as `answer.toml`. Generate a salted password hash (`openssl passwd -6`) and paste your SSH public key.
2. **First-Boot Hook**: Copy `first-boot.sh` to your TrueNAS HTTP asset directory. Ensure permissions are executable.
3. **Network Boot**: Select Network Boot in your host's BIOS (tap `F12` on Lenovo/Dell). In the `netboot.xyz` menu, select `Custom Menu` -> `Proxmox VE 9.2 - Automated Install`.
4. Walk away. The node will install without prompts, reboot, execute `first-boot.sh`, and join your cluster.
