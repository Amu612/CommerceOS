variable "name" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }

variable "allowed_cidr_blocks" {
  type    = list(string)
  default = []
}

variable "engine_version" {
  type    = string
  default = "7.1"
}

variable "node_type" {
  type    = string
  default = "cache.t4g.micro"
}

variable "num_cache_clusters" {
  type        = number
  description = "1 = single node (dev), 2+ = primary + replica with automatic failover (prod)"
  default     = 1
}

variable "transit_encryption_enabled" {
  type    = bool
  default = false
}

variable "snapshot_retention_limit" {
  type    = number
  default = 1
}

variable "apply_immediately" {
  type    = bool
  default = true
}

variable "tags" {
  type    = map(string)
  default = {}
}
