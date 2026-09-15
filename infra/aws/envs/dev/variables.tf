variable "region" {
  type    = string
  default = "us-east-1"
}

variable "image_tag" {
  description = "Docker image tag to deploy (git SHA in CI). The very first apply needs a real tag already pushed to ECR — see infra/aws/README.md."
  type        = string
  default     = "bootstrap"
}

variable "domain_name" {
  description = "Optional custom domain (e.g. dev.commerceos.example.com). Leave null to use the ALB's own DNS name."
  type        = string
  default     = null
}

variable "acm_certificate_arn" {
  description = "ACM cert ARN in `region` for the ALB HTTPS listener. Leave null to serve plain HTTP."
  type        = string
  default     = null
}

variable "hosted_zone_id" {
  description = "Route53 hosted zone id, only used when domain_name is set"
  type        = string
  default     = null
}

variable "alert_email" {
  type    = string
  default = null
}

# ── LLM ──────────────────────────────────────────────────────
variable "llm_provider" {
  type    = string
  default = "deterministic" # cheap/free for dev; flip to groq|openai|anthropic|bedrock once a key is set
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

variable "tags" {
  type = map(string)
  default = {
    Project     = "CommerceOS"
    Environment = "dev"
    ManagedBy   = "terraform"
  }
}
