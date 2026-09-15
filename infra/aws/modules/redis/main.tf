terraform {
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.60" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

resource "random_password" "auth_token" {
  count   = var.transit_encryption_enabled ? 1 : 0
  length  = 32
  special = false
}

resource "aws_elasticache_subnet_group" "this" {
  name       = "${var.name}-redis"
  subnet_ids = var.private_subnet_ids
}

resource "aws_security_group" "redis" {
  name_prefix = "${var.name}-redis-"
  description = "ElastiCache Redis — inbound 6379 from the app tier only"
  vpc_id      = var.vpc_id
  tags        = merge(var.tags, { Name = "${var.name}-redis-sg" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "redis_from_app" {
  for_each          = toset(var.allowed_cidr_blocks)
  security_group_id = aws_security_group.redis.id
  cidr_ipv4         = each.value
  from_port         = 6379
  to_port           = 6379
  ip_protocol       = "tcp"
  description       = "Redis from the private app subnets"
}

resource "aws_vpc_security_group_egress_rule" "redis_all" {
  security_group_id = aws_security_group.redis.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id = var.name
  description          = "CommerceOS event bus / cache / replay state"

  engine         = "redis"
  engine_version = var.engine_version
  node_type      = var.node_type
  port           = 6379

  num_cache_clusters         = var.num_cache_clusters
  automatic_failover_enabled = var.num_cache_clusters > 1
  multi_az_enabled           = var.num_cache_clusters > 1

  subnet_group_name  = aws_elasticache_subnet_group.this.name
  security_group_ids = [aws_security_group.redis.id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = var.transit_encryption_enabled
  auth_token                 = var.transit_encryption_enabled ? random_password.auth_token[0].result : null

  snapshot_retention_limit   = var.snapshot_retention_limit
  snapshot_window            = "05:00-06:00"
  maintenance_window         = "mon:06:30-mon:07:30"
  apply_immediately          = var.apply_immediately
  auto_minor_version_upgrade = true

  tags = var.tags
}
