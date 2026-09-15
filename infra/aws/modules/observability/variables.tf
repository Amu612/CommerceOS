variable "name" { type = string }
variable "region" { type = string }
variable "alert_email" {
  type    = string
  default = null
}

variable "alb_arn_suffix" { type = string }
variable "cluster_name" { type = string }
variable "api_service_name" { type = string }
variable "rds_instance_id" { type = string }

variable "tags" {
  type    = map(string)
  default = {}
}
