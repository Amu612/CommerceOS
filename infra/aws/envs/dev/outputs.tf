output "public_url" {
  value = local.public_url
}

output "alb_dns_name" {
  value = module.alb.alb_dns_name
}

output "ecr_repository_urls" {
  value = module.ecr.repository_urls
}

output "rds_endpoint" {
  value = module.rds.endpoint
}

output "redis_endpoint" {
  value = module.redis.primary_endpoint
}

output "datasets_bucket" {
  value = module.datasets.bucket_name
}

output "ecs_cluster_name" {
  value = module.ecs.cluster_name
}

output "ssm_parameter_prefix" {
  description = "Where the deploy workflow reads this environment's ECS/network names from"
  value       = "/commerceos/dev"
}
