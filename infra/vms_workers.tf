# General Compute Worker Nodes
resource "proxmox_virtual_environment_vm" "workers" {
  for_each = var.workers

  name        = each.key
  vm_id       = each.value.vmid
  node_name   = each.value.target_node
  description = "RKE2 Compute Worker Node: ${each.key} managed by OpenTofu"
  tags        = ["k8s", "worker", "compute"]

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

  # Optional PCIe GPU Pass-Through (e.g. Intel QuickSync iGPU 0000:00:02.0 or mapped device "iGPU")
  dynamic "hostpci" {
    for_each = each.value.passthrough_gpu != null ? [each.value.passthrough_gpu] : []
    content {
      device  = "hostpci0"
      mapping = can(regex("^[0-9a-fA-F]{4}:", hostpci.value)) ? null : hostpci.value
      id      = can(regex("^[0-9a-fA-F]{4}:", hostpci.value)) ? hostpci.value : null
      pcie    = false
      rombar  = true
      xvga    = false
    }
  }

  operating_system {
    type = "l26"
  }
}
