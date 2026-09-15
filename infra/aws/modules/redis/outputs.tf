output "primary_endpoint" {
  value = aws_elasticache_replication_group.this.primary_endpoint_address
}

output "security_group_id" {
  value = aws_security_group.redis.id
}

output "redis_url" {
  description = "redis[s]://[:token@]host:6379/0 for app.core.settings.REDIS_URL"
  value = var.transit_encryption_enabled ? (
    "rediss://:${random_password.auth_token[0].result}@${aws_elasticache_replication_group.this.primary_endpoint_address}:6379/0"
    ) : (
    "redis://${aws_elasticache_replication_group.this.primary_endpoint_address}:6379/0"
  )
  sensitive = true
}
