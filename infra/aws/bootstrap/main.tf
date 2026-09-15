terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.60" }
    tls = { source = "hashicorp/tls", version = "~> 4.0" }
  }
  # Deliberately no remote backend here — this config *creates* the remote
  # backend the envs/{dev,prod} stacks use. Run it once, by hand, with local
  # state (commit the resulting state to nowhere; re-running `apply` is safe
  # and idempotent — see README.md in this directory), then never touch it
  # again unless you're rotating the OIDC trust or renaming the project.
}

provider "aws" {
  region = var.region
}

data "aws_caller_identity" "current" {}

# ── Terraform remote state ──────────────────────────────────
resource "aws_s3_bucket" "tfstate" {
  bucket = "commerceos-tfstate-${data.aws_caller_identity.current.account_id}"
  tags   = { Project = "CommerceOS", Purpose = "terraform-state" }
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_dynamodb_table" "tfstate_lock" {
  name         = "commerceos-tfstate-lock"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  tags = { Project = "CommerceOS", Purpose = "terraform-state-lock" }
}

# ── GitHub OIDC — lets Actions assume AWS roles with no long-lived keys ──
data "tls_certificate" "github" {
  url = "https://token.actions.githubusercontent.com/.well-known/openid-configuration"
}

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.github.certificates[0].sha1_fingerprint]
}

locals {
  # Only these repo/ref combinations may assume the deploy roles.
  # Update if the repo is renamed/forked.
  github_repo = "Amu612/CommerceOS"
  allowed_subs = [
    "repo:${local.github_repo}:ref:refs/heads/main",
    "repo:${local.github_repo}:ref:refs/tags/*",
    "repo:${local.github_repo}:environment:dev",
    "repo:${local.github_repo}:environment:prod",
  ]
}

data "aws_iam_policy_document" "github_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.allowed_subs
    }
  }
}

# ── Role 1: application deploy (ci.yml build, deploy.yml ship) ──────────
# Build/push images, run the migrate task, roll api/worker/frontend forward.
# No IAM-write, no ability to touch state/infra it didn't create.
resource "aws_iam_role" "deploy" {
  name               = "commerceos-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_trust.json
  tags               = { Project = "CommerceOS" }
}

data "aws_iam_policy_document" "deploy" {
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid = "EcrPush"
    actions = [
      "ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage", "ecr:PutImage", "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:DescribeRepositories",
      "ecr:DescribeImages",
    ]
    resources = ["arn:aws:ecr:*:${data.aws_caller_identity.current.account_id}:repository/commerceos/*"]
  }
  statement {
    sid = "EcsDeploy"
    actions = [
      "ecs:DescribeServices", "ecs:DescribeTaskDefinition", "ecs:DescribeTasks",
      "ecs:ListTasks", "ecs:RunTask", "ecs:UpdateService", "ecs:RegisterTaskDefinition",
      "ecs:TagResource",
    ]
    resources = ["*"] # ECS task-def/run-task calls don't support resource-level scoping well; narrowed via PassRole below
  }
  statement {
    sid       = "PassEcsRoles"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/commerceos-*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
  statement {
    sid       = "ReadDeployConfig"
    actions   = ["ssm:GetParameter", "ssm:GetParametersByPath"]
    resources = ["arn:aws:ssm:*:${data.aws_caller_identity.current.account_id}:parameter/commerceos/*"]
  }
  statement {
    sid       = "ReadLogsForDebug"
    actions   = ["logs:GetLogEvents", "logs:DescribeLogStreams"]
    resources = ["arn:aws:logs:*:${data.aws_caller_identity.current.account_id}:log-group:/ecs/commerceos-*"]
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "commerceos-deploy"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}

# ── Role 2: infra (infra.yml — terraform plan/apply) ─────────────────────
# Broad by necessity (Terraform provisions VPC/RDS/ElastiCache/ECS/ALB/IAM),
# scoped to resources this project actually creates. Tighten further once the
# module set stabilizes; if `terraform apply` hits AccessDenied on something
# legitimate, add that one action here rather than widening to "*".
resource "aws_iam_role" "terraform" {
  name               = "commerceos-terraform"
  assume_role_policy = data.aws_iam_policy_document.github_trust.json
  tags               = { Project = "CommerceOS" }
}

data "aws_iam_policy_document" "terraform" {
  statement {
    sid = "StateBackend"
    actions = [
      "s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket",
    ]
    resources = [aws_s3_bucket.tfstate.arn, "${aws_s3_bucket.tfstate.arn}/*"]
  }
  statement {
    sid       = "StateLock"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
    resources = [aws_dynamodb_table.tfstate_lock.arn]
  }
  statement {
    sid = "CoreInfra"
    actions = [
      "ec2:*", "rds:*", "elasticache:*", "elasticloadbalancing:*",
      "ecs:*", "application-autoscaling:*", "logs:*", "cloudwatch:*",
      "sns:*", "ssm:*", "secretsmanager:*", "s3:*", "ecr:*",
    ]
    resources = ["*"]
  }
  statement {
    sid = "IamForServiceRoles"
    actions = [
      "iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:TagRole",
      "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:GetRolePolicy",
      "iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies", "iam:PassRole", "iam:ListInstanceProfilesForRole",
    ]
    resources = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/commerceos-*"]
  }
}

resource "aws_iam_role_policy" "terraform" {
  name   = "commerceos-terraform"
  role   = aws_iam_role.terraform.id
  policy = data.aws_iam_policy_document.terraform.json
}
