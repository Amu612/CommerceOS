variable "name" { type = string }
variable "vpc_id" { type = string }
variable "public_subnet_ids" { type = list(string) }

variable "acm_certificate_arn" {
  type        = string
  default     = null
  description = "ACM cert ARN in the ALB's own region. Leave null to serve plain HTTP (no domain yet)."
}

variable "api_container_port" {
  type    = number
  default = 8000
}

variable "frontend_container_port" {
  type    = number
  default = 8080
}

variable "enable_deletion_protection" {
  type    = bool
  default = false
}

variable "tags" {
  type    = map(string)
  default = {}
}
