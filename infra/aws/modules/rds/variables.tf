variable "name" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }

variable "allowed_cidr_blocks" {
  type        = list(string)
  description = "CIDR blocks (typically the private app subnets) allowed to reach Postgres on 5432"
  default     = []
}

variable "db_name" {
  type    = string
  default = "commerceos"
}

variable "master_username" {
  type    = string
  default = "commerceos_app"
}

variable "engine_version" {
  type    = string
  default = "15.8"
}

variable "parameter_group_family" {
  type    = string
  default = "postgres15"
}

variable "instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "allocated_storage" {
  type    = number
  default = 20
}

variable "max_allocated_storage" {
  type    = number
  default = 100
}

variable "multi_az" {
  type    = bool
  default = false
}

variable "backup_retention_period" {
  type    = number
  default = 7
}

variable "deletion_protection" {
  type    = bool
  default = false
}

variable "skip_final_snapshot" {
  type    = bool
  default = true
}

variable "performance_insights_enabled" {
  type    = bool
  default = false
}

variable "apply_immediately" {
  type    = bool
  default = true
}

variable "create_read_replica" {
  type    = bool
  default = false
}

variable "replica_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "tags" {
  type    = map(string)
  default = {}
}
