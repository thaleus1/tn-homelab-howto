# Control Plane Master Nodes (etcd Quorum)
resource "proxmox_virtual_environment_vm" "masters" {
  for_each = var.masters

  name        = each.key
  vm_id       = each.value.vmid
  node_name   = each.value.target_node
  description = "RKE2 Control Plane Node: ${each.key} managed by OpenTofu"
  tags        = ["k8s", "control-plane", "master"]

  cpu {
    cores = each.value.cores
    type  = "host"
  }

  memory {
    dedicated = each.value.memory
  }

  agent {
    enabled = true
  }

  smbios {
    serial = each.key
  }

  initialization {
    ip_config {
      ipv4 {
        address = "${each.value.ip}/${var.cidr_mask}"
        gateway = var.gateway
      }
    }
    dns {
      servers = var.nameservers
    }
    user_data_file_id = proxmox_virtual_environment_file.cloud_init_user_data.id
  }

  disk {
    datastore_id = var.vm_storage_pool
    file_id      = var.cloud_image_id
    interface    = "scsi0"
    size         = each.value.disk_size
    discard      = "on"
    ssd          = true
  }

  network_device {
    bridge  = var.network_bridge
    vlan_id = var.vlan_tag
    model   = "virtio"
  }

  lifecycle {
    ignore_changes = [
      initialization[0].user_data_file_id,
      initialization[0].user_account,
      disk[0].file_id,
    ]
  }

  operating_system {
    type = "l26"
  }
}
