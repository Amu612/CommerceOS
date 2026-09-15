output "identifier" {
  value = aws_db_instance.this.identifier
}

output "endpoint" {
  value = aws_db_instance.this.address
}

output "port" {
  value = aws_db_instance.this.port
}

output "db_name" {
  value = aws_db_instance.this.db_name
}

output "master_username" {
  value = aws_db_instance.this.username
}

output "master_password" {
  value     = random_password.master.result
  sensitive = true
}

output "security_group_id" {
  value = aws_security_group.db.id
}

output "database_url" {
  description = "SQLAlchemy-compatible connection string for the primary instance"
  value       = "postgresql://${aws_db_instance.this.username}:${random_password.master.result}@${aws_db_instance.this.address}:${aws_db_instance.this.port}/${aws_db_instance.this.db_name}"
  sensitive   = true
}

output "replica_endpoint" {
  value = var.create_read_replica ? aws_db_instance.replica[0].address : null
}

output "database_read_url" {
  description = "Connection string for the read replica, falls back to null when no replica exists"
  value       = var.create_read_replica ? "postgresql://${aws_db_instance.this.username}:${random_password.master.result}@${aws_db_instance.replica[0].address}:${aws_db_instance.this.port}/${aws_db_instance.this.db_name}" : null
  sensitive   = true
}
