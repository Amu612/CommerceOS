terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.60" }
  }
}

data "aws_caller_identity" "current" {}

# ── Cluster ──────────────────────────────────────────────────
resource "aws_ecs_cluster" "this" {
  name = var.name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = var.tags
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = 1
  }
}

# ── Networking ───────────────────────────────────────────────
resource "aws_security_group" "tasks" {
  name_prefix = "${var.name}-tasks-"
  description = "ECS tasks (api/worker/frontend/migrate) — inbound from the ALB only"
  vpc_id      = var.vpc_id
  tags        = merge(var.tags, { Name = "${var.name}-tasks-sg" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "from_alb_api" {
  security_group_id            = aws_security_group.tasks.id
  referenced_security_group_id = var.alb_security_group_id
  from_port                    = var.api_container_port
  to_port                      = var.api_container_port
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "from_alb_frontend" {
  security_group_id            = aws_security_group.tasks.id
  referenced_security_group_id = var.alb_security_group_id
  from_port                    = var.frontend_container_port
  to_port                      = var.frontend_container_port
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "tasks_all" {
  security_group_id = aws_security_group.tasks.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# ── Logs ─────────────────────────────────────────────────────
resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${var.name}/api"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}
resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${var.name}/worker"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}
resource "aws_cloudwatch_log_group" "frontend" {
  name              = "/ecs/${var.name}/frontend"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}
resource "aws_cloudwatch_log_group" "migrate" {
  name              = "/ecs/${var.name}/migrate"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

# ── IAM ──────────────────────────────────────────────────────
data "aws_iam_policy_document" "assume_ecs_tasks" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Execution role: pulls the image from ECR, ships logs, and resolves the
# `secrets` block of each container definition at task launch.
resource "aws_iam_role" "execution" {
  name_prefix        = "${var.name}-exec-"
  assume_role_policy = data.aws_iam_policy_document.assume_ecs_tasks.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  count = length(var.app_secrets) > 0 ? 1 : 0
  name  = "read-app-secrets"
  role  = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = distinct(values(var.app_secrets))
    }]
  })
}

# Task role: what the *application code* is allowed to call.
resource "aws_iam_role" "task" {
  name_prefix        = "${var.name}-task-"
  assume_role_policy = data.aws_iam_policy_document.assume_ecs_tasks.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "task_logs_metrics" {
  name = "logs-and-metrics"
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = ["cloudwatch:PutMetricData"]
      Resource  = "*"
      Condition = { StringEquals = { "cloudwatch:namespace" = "CommerceOS" } }
    }]
  })
}

resource "aws_iam_role_policy" "task_datasets_s3" {
  count = var.datasets_bucket_arn != null ? 1 : 0
  name  = "read-datasets-bucket"
  role  = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:ListBucket"]
      Resource = [var.datasets_bucket_arn, "${var.datasets_bucket_arn}/*"]
    }]
  })
}

resource "aws_iam_role_policy" "task_bedrock" {
  count = var.enable_bedrock ? 1 : 0
  name  = "invoke-bedrock"
  role  = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy" "task_execute_command" {
  count = var.enable_execute_command ? 1 : 0
  name  = "ecs-exec"
  role  = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "ssmmessages:CreateControlChannel",
        "ssmmessages:CreateDataChannel",
        "ssmmessages:OpenControlChannel",
        "ssmmessages:OpenDataChannel",
      ]
      Resource = "*"
    }]
  })
}

# ── Helpers ──────────────────────────────────────────────────
locals {
  app_env_list     = [for k, v in var.app_environment : { name = k, value = v }]
  app_secrets_list = [for k, v in var.app_secrets : { name = k, valueFrom = v }]

  common_log_options = {
    "awslogs-region"        = var.region
    "awslogs-stream-prefix" = "ecs"
  }
}

# ── Task definitions ─────────────────────────────────────────
resource "aws_ecs_task_definition" "api" {
  family                   = "${var.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name         = "api"
    image        = var.api_image
    essential    = true
    portMappings = [{ containerPort = var.api_container_port, protocol = "tcp" }]
    environment  = local.app_env_list
    secrets      = local.app_secrets_list
    logConfiguration = {
      logDriver = "awslogs"
      options   = merge(local.common_log_options, { "awslogs-group" = aws_cloudwatch_log_group.api.name })
    }
    healthCheck = {
      command     = ["CMD-SHELL", "curl -fsS http://localhost:${var.api_container_port}/health || exit 1"]
      interval    = 15
      timeout     = 5
      retries     = 5
      startPeriod = 30
    }
  }])

  tags = var.tags
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name        = "worker"
    image       = var.api_image
    essential   = true
    command     = ["python", "-m", "app.worker.main"]
    environment = local.app_env_list
    secrets     = local.app_secrets_list
    logConfiguration = {
      logDriver = "awslogs"
      options   = merge(local.common_log_options, { "awslogs-group" = aws_cloudwatch_log_group.worker.name })
    }
  }])

  tags = var.tags
}

resource "aws_ecs_task_definition" "frontend" {
  family                   = "${var.name}-frontend"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.frontend_cpu
  memory                   = var.frontend_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name         = "frontend"
    image        = var.frontend_image
    essential    = true
    portMappings = [{ containerPort = var.frontend_container_port, protocol = "tcp" }]
    logConfiguration = {
      logDriver = "awslogs"
      options   = merge(local.common_log_options, { "awslogs-group" = aws_cloudwatch_log_group.frontend.name })
    }
  }])

  tags = var.tags
}

# One-shot: `alembic upgrade head` + warehouse seed + user seed. Not a
# service — CI (or the runbook's manual path) invokes it with
# `aws ecs run-task` after every image build.
resource "aws_ecs_task_definition" "migrate" {
  family                   = "${var.name}-migrate"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.migrate_cpu
  memory                   = var.migrate_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name        = "migrate"
    image       = var.api_image
    essential   = true
    command     = var.migrate_command
    environment = local.app_env_list
    secrets     = local.app_secrets_list
    logConfiguration = {
      logDriver = "awslogs"
      options   = merge(local.common_log_options, { "awslogs-group" = aws_cloudwatch_log_group.migrate.name })
    }
  }])

  tags = var.tags
}

# ── Services ─────────────────────────────────────────────────
resource "aws_ecs_service" "api" {
  name                   = "${var.name}-api"
  cluster                = aws_ecs_cluster.this.id
  task_definition        = aws_ecs_task_definition.api.arn
  desired_count          = var.api_desired_count
  launch_type            = "FARGATE"
  enable_execute_command = var.enable_execute_command

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.api_target_group_arn
    container_name   = "api"
    container_port   = var.api_container_port
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = 30

  lifecycle {
    ignore_changes = [task_definition] # CD updates this via `ecs update-service --force-new-deployment`
  }

  tags = var.tags
}

resource "aws_ecs_service" "worker" {
  name                   = "${var.name}-worker"
  cluster                = aws_ecs_cluster.this.id
  task_definition        = aws_ecs_task_definition.worker.arn
  desired_count          = 1 # single replay writer — do not scale (Redis-locked)
  launch_type            = "FARGATE"
  enable_execute_command = var.enable_execute_command

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  lifecycle {
    ignore_changes = [task_definition]
  }

  tags = var.tags
}

resource "aws_ecs_service" "frontend" {
  name                   = "${var.name}-frontend"
  cluster                = aws_ecs_cluster.this.id
  task_definition        = aws_ecs_task_definition.frontend.arn
  desired_count          = var.frontend_desired_count
  launch_type            = "FARGATE"
  enable_execute_command = var.enable_execute_command

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.frontend_target_group_arn
    container_name   = "frontend"
    container_port   = var.frontend_container_port
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = 30

  lifecycle {
    ignore_changes = [task_definition]
  }

  tags = var.tags
}

# ── Autoscaling (api only) ────────────────────────────────────
resource "aws_appautoscaling_target" "api" {
  max_capacity       = var.api_max_count
  min_capacity       = var.api_min_count
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.api.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "api_cpu" {
  name               = "${var.name}-api-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.api.resource_id
  scalable_dimension = aws_appautoscaling_target.api.scalable_dimension
  service_namespace  = aws_appautoscaling_target.api.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value       = var.api_cpu_target
    scale_in_cooldown  = 120
    scale_out_cooldown = 60
  }
}

# ── SSM parameters — how the deploy workflow finds this env's resources
# without a hand-maintained netcfg.json going stale. ──────────────────
locals {
  ssm_prefix = "/commerceos/${var.environment}"
}

resource "aws_ssm_parameter" "cluster_name" {
  name  = "${local.ssm_prefix}/ecs/cluster"
  type  = "String"
  value = aws_ecs_cluster.this.name
}

resource "aws_ssm_parameter" "service_api" {
  name  = "${local.ssm_prefix}/ecs/service_api"
  type  = "String"
  value = aws_ecs_service.api.name
}

resource "aws_ssm_parameter" "service_worker" {
  name  = "${local.ssm_prefix}/ecs/service_worker"
  type  = "String"
  value = aws_ecs_service.worker.name
}

resource "aws_ssm_parameter" "service_frontend" {
  name  = "${local.ssm_prefix}/ecs/service_frontend"
  type  = "String"
  value = aws_ecs_service.frontend.name
}

resource "aws_ssm_parameter" "api_task_family" {
  name  = "${local.ssm_prefix}/ecs/api_task_family"
  type  = "String"
  value = aws_ecs_task_definition.api.family
}

resource "aws_ssm_parameter" "worker_task_family" {
  name  = "${local.ssm_prefix}/ecs/worker_task_family"
  type  = "String"
  value = aws_ecs_task_definition.worker.family
}

resource "aws_ssm_parameter" "frontend_task_family" {
  name  = "${local.ssm_prefix}/ecs/frontend_task_family"
  type  = "String"
  value = aws_ecs_task_definition.frontend.family
}

resource "aws_ssm_parameter" "migrate_task_family" {
  name  = "${local.ssm_prefix}/ecs/migrate_task_family"
  type  = "String"
  value = aws_ecs_task_definition.migrate.family
}

resource "aws_ssm_parameter" "subnets" {
  name  = "${local.ssm_prefix}/network/private_subnet_ids"
  type  = "StringList"
  value = join(",", var.private_subnet_ids)
}

resource "aws_ssm_parameter" "tasks_security_group" {
  name  = "${local.ssm_prefix}/network/tasks_security_group_id"
  type  = "String"
  value = aws_security_group.tasks.id
}
