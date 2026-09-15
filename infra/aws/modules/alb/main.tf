terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.60" }
  }
}

resource "aws_security_group" "alb" {
  name_prefix = "${var.name}-alb-"
  description = "Public ALB — HTTP/HTTPS from the internet"
  vpc_id      = var.vpc_id
  tags        = merge(var.tags, { Name = "${var.name}-alb-sg" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "http" {
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  count             = var.acm_certificate_arn != null ? 1 : 0
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_lb" "this" {
  name               = var.name
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.public_subnet_ids

  enable_deletion_protection       = var.enable_deletion_protection
  enable_cross_zone_load_balancing = true
  idle_timeout                     = 90 # WebSocket connections outlive the 60s default

  tags = var.tags
}

resource "aws_lb_target_group" "api" {
  name        = "${var.name}-api"
  port        = var.api_container_port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    path                = "/health"
    healthy_threshold   = 2
    unhealthy_threshold = 5
    interval            = 15
    timeout             = 5
    matcher             = "200"
  }

  deregistration_delay = 30
  tags                 = var.tags
}

resource "aws_lb_target_group" "frontend" {
  name        = "${var.name}-fe"
  port        = var.frontend_container_port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    path                = "/"
    healthy_threshold   = 2
    unhealthy_threshold = 5
    interval            = 15
    timeout             = 5
    matcher             = "200"
  }

  deregistration_delay = 15
  tags                 = var.tags
}

# ── HTTP listener ──────────────────────────────────────────
# With a cert: redirect everything to HTTPS. Without one: serve the app
# directly over HTTP (fine for a first dev deploy without a domain yet).
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  dynamic "default_action" {
    for_each = var.acm_certificate_arn != null ? [1] : []
    content {
      type = "redirect"
      redirect {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }

  dynamic "default_action" {
    for_each = var.acm_certificate_arn == null ? [1] : []
    content {
      type             = "forward"
      target_group_arn = aws_lb_target_group.frontend.arn
    }
  }
}

resource "aws_lb_listener" "https" {
  count             = var.acm_certificate_arn != null ? 1 : 0
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.acm_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.frontend.arn
  }
}

locals {
  # Rules attach to whichever listener is actually serving app traffic:
  # HTTPS when a cert is configured, otherwise plain HTTP.
  app_listener_arn = var.acm_certificate_arn != null ? aws_lb_listener.https[0].arn : aws_lb_listener.http.arn
}

resource "aws_lb_listener_rule" "api" {
  listener_arn = local.app_listener_arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }

  condition {
    path_pattern {
      values = ["/api/*", "/ws/*", "/health", "/health/*", "/docs", "/redoc", "/openapi.json"]
    }
  }
}
