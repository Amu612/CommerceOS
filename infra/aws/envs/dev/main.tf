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
  name = "commerceos-dev"
}

# ── Network ──────────────────────────────────────────────────
module "network" {
  source     = "../../modules/network"
  name       = local.name
  region     = var.region
  vpc_cidr   = "10.20.0.0/16"
  az_count   = 2
  single_nat = true # cost saving for dev; prod uses one NAT per AZ
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

  instance_class          = "db.t4g.micro"
  allocated_storage       = 20
  multi_az                = false
  backup_retention_period = 3
  deletion_protection     = false
  skip_final_snapshot     = true
  create_read_replica     = false

  tags = var.tags
}

module "redis" {
  source              = "../../modules/redis"
  name                = "${local.name}-redis"
  vpc_id              = module.network.vpc_id
  private_subnet_ids  = module.network.private_subnet_ids
  allowed_cidr_blocks = [module.network.vpc_cidr]

  node_type                  = "cache.t4g.micro"
  num_cache_clusters         = 1
  transit_encryption_enabled = false

  tags = var.tags
}

# ── Secrets ──────────────────────────────────────────────────
resource "random_password" "jwt_secret" {
  length  = 48
  special = false
}

resource "random_password" "seed_admin_password" {
  length           = 20
  special          = true
  override_special = "!@#%"
}

module "secrets" {
  source = "../../modules/secrets"
  prefix = "commerceos/dev"
  secrets = {
    "database-url"        = module.rds.database_url
    "redis-url"           = module.redis.redis_url
    "jwt-secret"          = random_password.jwt_secret.result
    "seed-admin-password" = random_password.seed_admin_password.result
    "groq-api-key"        = var.groq_api_key
    "openai-api-key"      = var.openai_api_key
  }
  tags = var.tags
}

# ── Load balancer ────────────────────────────────────────────
module "alb" {
  source              = "../../modules/alb"
  name                = local.name
  vpc_id              = module.network.vpc_id
  public_subnet_ids   = module.network.public_subnet_ids
  acm_certificate_arn = var.acm_certificate_arn
  tags                = var.tags
}

# ── Compute ──────────────────────────────────────────────────
locals {
  public_url = var.domain_name != null ? "https://${var.domain_name}" : module.alb.public_url

  app_environment = {
    ENVIRONMENT                     = "development"
    PROJECT_NAME                    = "CommerceOS AI"
    PORT                            = "8000"
    WORKERS                         = "2"
    DB_POOL_SIZE                    = "10"
    REDIS_ENABLED                   = "true"
    CORS_ORIGINS                    = local.public_url
    TRUSTED_HOSTS                   = "*"
    EXPOSE_DOCS                     = "true"
    AUTH_ENFORCED                   = "false" # dev convenience — see docs/gap-analysis.md; flip to true once the SPA has a login screen
    JWT_TTL_MINUTES                 = "720"
    LLM_PROVIDER                    = var.llm_provider
    LLM_MODEL                       = var.llm_model
    BEDROCK_REGION                  = var.region
    DATASET_DIR                     = "s3://${module.datasets.bucket_name}/raw"
    REPLAY_MAX_ORDERS               = "15000"
    LOG_LEVEL                       = "INFO"
    LOG_JSON                        = "true"
    METRICS_ENABLED                 = "true"
    PRICING_COMPETITOR_FEED_ENABLED = "false"
  }

  app_secrets = {
    DATABASE_URL        = module.secrets.secret_arns["database-url"]
    REDIS_URL           = module.secrets.secret_arns["redis-url"]
    JWT_SECRET          = module.secrets.secret_arns["jwt-secret"]
    SEED_ADMIN_PASSWORD = module.secrets.secret_arns["seed-admin-password"]
    GROQ_API_KEY        = module.secrets.secret_arns["groq-api-key"]
    OPENAI_API_KEY      = module.secrets.secret_arns["openai-api-key"]
  }
}

module "ecs" {
  source             = "../../modules/ecs"
  name               = local.name
  environment        = "dev"
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

  # dev sizing — module defaults (0.5 vCPU/1GB api, 0.25/0.5 worker+frontend, 1 task each)
  api_min_count = 1
  api_max_count = 2

  tags = var.tags
}

# deploy.yml reads this instead of a hand-maintained GitHub variable, so the
# smoke-test URL can never drift from what Terraform actually provisioned.
resource "aws_ssm_parameter" "public_url" {
  name  = "/commerceos/dev/public_url"
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
