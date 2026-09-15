terraform {
  required_version = ">= 1.7"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.60" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = var.tags
  }
}

locals {
  name = "commerceos-prod"
}

# ── Network ──────────────────────────────────────────────────
module "network" {
  source     = "../../modules/network"
  name       = local.name
  region     = var.region
  vpc_cidr   = "10.30.0.0/16"
  az_count   = 2
  single_nat = false # one NAT per AZ — no cross-AZ single point of failure
  tags       = var.tags
}

# ── Images + datasets ────────────────────────────────────────
module "ecr" {
  source           = "../../modules/ecr"
  name             = "commerceos"
  repository_names = ["api", "frontend"]
  tags             = var.tags
}

module "datasets" {
  source = "../../modules/datasets"
  name   = local.name
  tags   = var.tags
}

# ── Data stores ──────────────────────────────────────────────
module "rds" {
  source              = "../../modules/rds"
  name                = "${local.name}-db"
  vpc_id              = module.network.vpc_id
  private_subnet_ids  = module.network.private_subnet_ids
  allowed_cidr_blocks = [module.network.vpc_cidr]

  instance_class               = "db.t4g.medium"
  allocated_storage            = 100
  max_allocated_storage        = 500
  multi_az                     = true
  backup_retention_period      = 14
  deletion_protection          = true
  skip_final_snapshot          = false
  performance_insights_enabled = true
  create_read_replica          = true
  replica_instance_class       = "db.t4g.medium"

  tags = var.tags
}

module "redis" {
  source              = "../../modules/redis"
  name                = "${local.name}-redis"
  vpc_id              = module.network.vpc_id
  private_subnet_ids  = module.network.private_subnet_ids
  allowed_cidr_blocks = [module.network.vpc_cidr]

  node_type                  = "cache.t4g.small"
  num_cache_clusters         = 2 # primary + replica, automatic failover
  transit_encryption_enabled = true
  snapshot_retention_limit   = 5

  tags = var.tags
}

# ── Secrets ──────────────────────────────────────────────────
resource "random_password" "jwt_secret" {
  length  = 48
  special = false
}

resource "random_password" "seed_admin_password" {
  length           = 24
  special          = true
  override_special = "!@#%"
}

module "secrets" {
  source = "../../modules/secrets"
  prefix = "commerceos/prod"
  secrets = {
    "database-url"        = module.rds.database_url
    "database-read-url"   = coalesce(module.rds.database_read_url, module.rds.database_url)
    "redis-url"           = module.redis.redis_url
    "jwt-secret"          = random_password.jwt_secret.result
    "seed-admin-password" = random_password.seed_admin_password.result
    "groq-api-key"        = var.groq_api_key
    "openai-api-key"      = var.openai_api_key
    "anthropic-api-key"   = var.anthropic_api_key
  }
  recovery_window_in_days = 30
  tags                    = var.tags
}

# ── Load balancer ────────────────────────────────────────────
module "alb" {
  source                     = "../../modules/alb"
  name                       = local.name
  vpc_id                     = module.network.vpc_id
  public_subnet_ids          = module.network.public_subnet_ids
  acm_certificate_arn        = var.acm_certificate_arn
  enable_deletion_protection = true
  tags                       = var.tags
}

# ── Compute ──────────────────────────────────────────────────
locals {
  public_url = var.domain_name != null ? "https://${var.domain_name}" : module.alb.public_url

  app_environment = {
    ENVIRONMENT                     = "production"
    PROJECT_NAME                    = "CommerceOS AI"
    PORT                            = "8000"
    WORKERS                         = "4"
    DB_POOL_SIZE                    = "20"
    REDIS_ENABLED                   = "true"
    CORS_ORIGINS                    = local.public_url
    TRUSTED_HOSTS                   = var.domain_name != null ? var.domain_name : "*"
    EXPOSE_DOCS                     = "false"
    AUTH_ENFORCED                   = "true"
    JWT_TTL_MINUTES                 = "720"
    LLM_PROVIDER                    = var.llm_provider
    LLM_MODEL                       = var.llm_model
    BEDROCK_REGION                  = var.region
    DATASET_DIR                     = "s3://${module.datasets.bucket_name}/raw"
    REPLAY_MAX_ORDERS               = "15000"
    LOG_LEVEL                       = "INFO"
    LOG_JSON                        = "true"
    METRICS_ENABLED                 = "true"
    OTEL_ENABLED                    = "true"
    PRICING_COMPETITOR_FEED_ENABLED = "false"
  }

  app_secrets = {
    DATABASE_URL        = module.secrets.secret_arns["database-url"]
    DATABASE_READ_URL   = module.secrets.secret_arns["database-read-url"]
    REDIS_URL           = module.secrets.secret_arns["redis-url"]
    JWT_SECRET          = module.secrets.secret_arns["jwt-secret"]
    SEED_ADMIN_PASSWORD = module.secrets.secret_arns["seed-admin-password"]
    GROQ_API_KEY        = module.secrets.secret_arns["groq-api-key"]
    OPENAI_API_KEY      = module.secrets.secret_arns["openai-api-key"]
    ANTHROPIC_API_KEY   = module.secrets.secret_arns["anthropic-api-key"]
  }
}

module "ecs" {
  source             = "../../modules/ecs"
  name               = local.name
  environment        = "prod"
  region             = var.region
  vpc_id             = module.network.vpc_id
  private_subnet_ids = module.network.private_subnet_ids

  alb_security_group_id     = module.alb.security_group_id
  api_target_group_arn      = module.alb.api_target_group_arn
  frontend_target_group_arn = module.alb.frontend_target_group_arn
  app_listener_arn          = module.alb.app_listener_arn

  api_image      = "${module.ecr.repository_urls["api"]}:${var.image_tag}"
  frontend_image = "${module.ecr.repository_urls["frontend"]}:${var.image_tag}"

  app_environment = local.app_environment
  app_secrets     = local.app_secrets

  datasets_bucket_arn = module.datasets.bucket_arn
  enable_bedrock      = var.llm_provider == "bedrock"

  api_cpu           = 1024
  api_memory        = 2048
  api_desired_count = 2
  api_min_count     = 2
  api_max_count     = 6

  worker_cpu    = 512
  worker_memory = 1024

  frontend_cpu           = 512
  frontend_memory        = 1024
  frontend_desired_count = 2

  log_retention_days = 30

  tags = var.tags
}

# deploy.yml reads this instead of a hand-maintained GitHub variable, so the
# smoke-test URL can never drift from what Terraform actually provisioned.
resource "aws_ssm_parameter" "public_url" {
  name  = "/commerceos/prod/public_url"
  type  = "String"
  value = local.public_url
}

# ── Observability ────────────────────────────────────────────
module "observability" {
  source           = "../../modules/observability"
  name             = local.name
  region           = var.region
  alert_email      = var.alert_email
  alb_arn_suffix   = module.alb.alb_arn_suffix
  cluster_name     = module.ecs.cluster_name
  api_service_name = module.ecs.api_service_name
  rds_instance_id  = module.rds.identifier
  tags             = var.tags
}
