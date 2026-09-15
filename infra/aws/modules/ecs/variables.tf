variable "name" {
  type        = string
  description = "Cluster + resource name prefix, e.g. \"commerceos-dev\""
}

variable "environment" {
  type        = string
  description = "development | production (also the SSM/CI environment key: dev | prod)"
}

variable "region" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }

variable "alb_security_group_id" { type = string }
variable "api_target_group_arn" { type = string }
variable "frontend_target_group_arn" { type = string }
variable "app_listener_arn" {
  type        = string
  description = "Forces services to wait for the ALB listener/rule before attaching"
}

variable "api_image" { type = string }
variable "frontend_image" { type = string }

variable "api_container_port" {
  type    = number
  default = 8000
}
variable "frontend_container_port" {
  type    = number
  default = 8080
}

# Shared by api / worker / migrate — all three run the same backend image.
variable "app_environment" {
  type    = map(string)
  default = {}
}
variable "app_secrets" {
  description = "map of ENV_VAR -> Secrets Manager ARN (or ARN:jsonKey::)"
  type        = map(string)
  default     = {}
}

variable "migrate_command" {
  type    = list(string)
  default = ["sh", "-c", "python -m scripts.sync_datasets; alembic upgrade head && python -m scripts.seed_users && (python -m scripts.seed_nexus_data || true)"]
}

# ── Sizing ──────────────────────────────────────────────────
variable "api_cpu" {
  type    = number
  default = 512
}
variable "api_memory" {
  type    = number
  default = 1024
}
variable "api_desired_count" {
  type    = number
  default = 1
}
variable "api_min_count" {
  type    = number
  default = 1
}
variable "api_max_count" {
  type    = number
  default = 4
}
variable "api_cpu_target" {
  type    = number
  default = 65
}

variable "worker_cpu" {
  type    = number
  default = 256
}
variable "worker_memory" {
  type    = number
  default = 512
}

variable "frontend_cpu" {
  type    = number
  default = 256
}
variable "frontend_memory" {
  type    = number
  default = 512
}
variable "frontend_desired_count" {
  type    = number
  default = 1
}

variable "migrate_cpu" {
  type    = number
  default = 512
}
variable "migrate_memory" {
  type    = number
  default = 1024
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "datasets_bucket_arn" {
  type    = string
  default = null
}

variable "enable_bedrock" {
  type    = bool
  default = false
}

variable "enable_execute_command" {
  description = "ECS Exec (interactive shell into a running task) for debugging"
  type        = bool
  default     = true
}

variable "tags" {
  type    = map(string)
  default = {}
}
