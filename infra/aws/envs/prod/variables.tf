variable "region" {
  type    = string
  default = "us-east-1"
}

variable "image_tag" {
  description = "Docker image tag to deploy (git SHA / vX.Y.Z tag in CI). The very first apply needs a real tag already pushed to ECR — see infra/aws/README.md."
  type        = string
  default     = "bootstrap"
}

variable "domain_name" {
  type    = string
  default = null
}

variable "acm_certificate_arn" {
  description = "REQUIRED in practice for prod — plain HTTP is allowed so terraform apply doesn't hard-fail without one, but ship a cert before real traffic."
  type        = string
  default     = null
}

variable "hosted_zone_id" {
  type    = string
  default = null
}

variable "alert_email" {
  type = string
  # Required for prod — CloudWatch alarms are silent without a subscriber.
}

variable "llm_provider" {
  type    = string
  default = "bedrock"
}

variable "llm_model" {
  type    = string
  default = ""
}

variable "groq_api_key" {
  type      = string
  default   = ""
  sensitive = true
}

variable "openai_api_key" {
  type      = string
  default   = ""
  sensitive = true
}

variable "anthropic_api_key" {
  type      = string
  default   = ""
  sensitive = true
}

variable "tags" {
  type = map(string)
  default = {
    Project     = "CommerceOS"
    Environment = "prod"
    ManagedBy   = "terraform"
  }
}
