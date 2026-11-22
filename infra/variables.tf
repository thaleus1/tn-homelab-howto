variable "proxmox_endpoint" {
  type        = string
  description = "Proxmox VE API endpoint URL (e.g. https://pve01.yourdomain.com:8006/)"
}

variable "proxmox_api_token" {
  type        = string
  description = "Proxmox VE API Token in format USER@REALM!TOKENID=UUID"
  sensitive   = true
}

variable "proxmox_insecure" {
  type        = bool
  description = "Whether to bypass TLS certificate validation for self-signed Proxmox certificates"
  default     = true
}

variable "gateway" {
  type        = string
  description = "Default IPv4 Gateway for Kubernetes nodes (e.g. 10.90.16.1 or 10.0.10.1)"
  default     = "10.90.16.1"
}

variable "cidr_mask" {
  type        = string
  description = "Subnet CIDR mask prefix length (e.g. '23' or '24')"
  default     = "23"
}

variable "nameservers" {
  type        = list(string)
  description = "DNS Nameservers for cluster VMs"
  default     = ["1.1.1.1", "8.8.8.8"]
}

variable "network_bridge" {
  type        = string
  description = "Proxmox network bridge interface"
  default     = "vmbr0"
}

variable "vlan_tag" {
  type        = number
  description = "VLAN Tag for the Kubernetes network (null for untagged)"
  default     = 90
}

variable "ssh_public_key" {
  type        = string
  description = "SSH public key to inject into all cluster nodes via Cloud-Init"
}

variable "default_user" {
  type        = string
  description = "Default administrative username created by Cloud-Init"
  default     = "admin"
}

variable "user_password_hash" {
  type        = string
  description = "Optional salted password hash for default user (generate via openssl passwd -6)"
  default     = null
}

variable "cloud_init_node" {
  type        = string
  description = "Proxmox node name to store cloud-init snippet (e.g. 'pve01')"
  default     = "pve01"
}

variable "cloud_image_id" {
  type        = string
  description = "Proxmox file ID for Ubuntu Cloud-Init image (e.g. local:iso/ubuntu-24.04-minimal-cloudimg-amd64.img)"
  default     = "local:iso/ubuntu-24.04-minimal-cloudimg-amd64.img"
}

variable "snippet_storage_pool" {
  type        = string
  description = "Proxmox storage pool that supports snippets (usually 'local' or shared storage)"
  default     = "local"
}

variable "vm_storage_pool" {
  type        = string
  description = "Proxmox storage pool for virtual disks (e.g. 'local-lvm' or 'local-zfs')"
  default     = "local-lvm"
}

# --- RKE2 Auto-Join Configuration ---

variable "rke2_server_url" {
  type        = string
  description = "URL of the primary RKE2 server or kube-vip (e.g. https://10.90.16.50:9345)"
  default     = "https://10.90.16.50:9345"
}

variable "rke2_token" {
  type        = string
  description = "Cluster join token (from /var/lib/rancher/rke2/server/node-token on server)"
  sensitive   = true
  default     = "REPLACE_WITH_YOUR_RKE2_CLUSTER_TOKEN"
}

variable "rke2_version" {
  type        = string
  description = "RKE2 version to install"
  default     = "v1.37.2+rke2r1"
}

# --- Node Maps ---

variable "masters" {
  type = map(object({
    vmid        = number
    target_node = string
    ip          = string
    cores       = number
    memory      = number
    disk_size   = number
  }))
  description = "Control Plane Master nodes configuration"
}

variable "storage_nodes" {
  type = map(object({
    vmid              = number
    target_node       = string
    ip                = string
    cores             = number
    memory            = number
    os_disk_size      = number
    data_disk_size    = number
    data_storage_pool = string
  }))
  description = "Dedicated Longhorn Storage nodes configuration"
}

variable "workers" {
  type = map(object({
    vmid            = number
    target_node     = string
    ip              = string
    cores           = number
    memory          = number
    disk_size       = number
    passthrough_gpu = optional(string, null)
  }))
  description = "Compute Worker nodes configuration"
}
