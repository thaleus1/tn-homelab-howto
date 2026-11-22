# Reusable Cloud-Init User Data Template
resource "proxmox_virtual_environment_file" "cloud_init_user_data" {
  content_type = "snippets"
  datastore_id = var.snippet_storage_pool
  node_name    = var.cloud_init_node

  source_raw {
    data = <<-EOF
      #cloud-config
      timezone: America/New_York
      package_update: true
      packages:
        - qemu-guest-agent
        - curl
        - nfs-common
        - open-iscsi
        - cryptsetup
        - dmsetup
        - iptables
        - socat
        - linux-modules-extra-generic
      users:
        - name: ${var.default_user}
          groups: sudo
          shell: /bin/bash
          sudo: ALL=(ALL) NOPASSWD:ALL
          lock_passwd: ${var.user_password_hash == null ? "true" : "false"}
          ${var.user_password_hash != null ? "passwd: \"${var.user_password_hash}\"" : ""}
          ssh_authorized_keys:
            - ${trimspace(var.ssh_public_key)}
      runcmd:
        - systemctl enable --now qemu-guest-agent || true
        - systemctl enable --now iscsid || true
        - modprobe br_netfilter
        - echo "br_netfilter" >> /etc/modules-load.d/k8s.conf
        - sysctl -w net.bridge.bridge-nf-call-iptables=1
        - sysctl -w net.ipv4.ip_forward=1
        - apt-get update && apt-get install -y linux-modules-extra-$(uname -r) && modprobe i915 || true
        - echo "i915" >> /etc/modules-load.d/k8s.conf
        - |
          NODE_NAME=$(cat /sys/class/dmi/id/product_serial 2>/dev/null || true)
          if [ -n "$NODE_NAME" ]; then
            hostnamectl set-hostname "$NODE_NAME"
            echo "$NODE_NAME" > /etc/hostname
          else
            NODE_NAME=$(hostname)
          fi
          mkdir -p /etc/rancher/rke2
          cat <<RKE2CONFIG > /etc/rancher/rke2/config.yaml
          server: ${var.rke2_server_url}
          token: ${var.rke2_token}
          cni: canal
          node-name: $NODE_NAME
          RKE2CONFIG
        - curl -sfL https://get.rke2.io | INSTALL_RKE2_TYPE="agent" INSTALL_RKE2_VERSION="${var.rke2_version}" sh -
        - systemctl enable --now rke2-agent.service
    EOF

    file_name = "k8s-cloud-init.yaml"
  }
}
