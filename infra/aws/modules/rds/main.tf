terraform {
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.60" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

resource "random_password" "master" {
  length      = 32
  special     = false # simplifies embedding in a DATABASE_URL without percent-encoding
  min_upper   = 1
  min_lower   = 1
  min_numeric = 1
}

resource "aws_db_subnet_group" "this" {
  name       = "${var.name}-db"
  subnet_ids = var.private_subnet_ids
  tags       = var.tags
}

resource "aws_security_group" "db" {
  name_prefix = "${var.name}-db-"
  description = "RDS PostgreSQL — inbound 5432 from the app tier only"
  vpc_id      = var.vpc_id
  tags        = merge(var.tags, { Name = "${var.name}-db-sg" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "db_from_app" {
  for_each          = toset(var.allowed_cidr_blocks)
  security_group_id = aws_security_group.db.id
  cidr_ipv4         = each.value
  from_port         = 5432
  to_port           = 5432
  ip_protocol       = "tcp"
  description       = "Postgres from the private app subnets"
}

resource "aws_vpc_security_group_egress_rule" "db_all" {
  security_group_id = aws_security_group.db.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_db_parameter_group" "this" {
  name_prefix = "${var.name}-pg-"
  family      = var.parameter_group_family
  tags        = var.tags

  parameter {
    name  = "log_min_duration_statement"
    value = "1000" # log queries slower than 1s
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_db_instance" "this" {
  identifier     = var.name
  engine         = "postgres"
  engine_version = var.engine_version

  instance_class        = var.instance_class
  allocated_storage     = var.allocated_storage
  max_allocated_storage = var.max_allocated_storage
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = var.db_name
  username = var.master_username
  password = random_password.master.result
  port     = 5432

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]
  parameter_group_name   = aws_db_parameter_group.this.name

  multi_az                        = var.multi_az
  publicly_accessible             = false
  backup_retention_period         = var.backup_retention_period
  backup_window                   = "03:00-04:00"
  maintenance_window              = "mon:04:30-mon:05:30"
  auto_minor_version_upgrade      = true
  deletion_protection             = var.deletion_protection
  skip_final_snapshot             = var.skip_final_snapshot
  final_snapshot_identifier       = var.skip_final_snapshot ? null : "${var.name}-final-${formatdate("YYYYMMDDhhmmss", timestamp())}"
  performance_insights_enabled    = var.performance_insights_enabled
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
  copy_tags_to_snapshot           = true
  apply_immediately               = var.apply_immediately

  tags = var.tags

  lifecycle {
    ignore_changes = [final_snapshot_identifier]
  }
}

# Optional read replica for analytics / heavy dashboards (prod only).
resource "aws_db_instance" "replica" {
  count = var.create_read_replica ? 1 : 0

  identifier                   = "${var.name}-replica"
  replicate_source_db          = aws_db_instance.this.identifier
  instance_class               = var.replica_instance_class
  publicly_accessible          = false
  storage_encrypted            = true
  auto_minor_version_upgrade   = true
  vpc_security_group_ids       = [aws_security_group.db.id]
  performance_insights_enabled = var.performance_insights_enabled
  skip_final_snapshot          = true
  tags                         = var.tags
}
