terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.60" }
  }
}

# One Secrets Manager secret per key in var.secrets, at
# "<prefix>/<key>" (e.g. "commerceos/dev/database-url"). Values are written
# once here; `deploy.yml`/`runbook.md` rotate them out-of-band with
# `aws secretsmanager put-secret-value` without Terraform ever seeing the
# rotated value (lifecycle.ignore_changes on the version).
locals {
  # for_each can't key off a sensitive value (the whole `var.secrets` map is
  # marked sensitive since some of its values are) — only the key *names*
  # (e.g. "database-url") drive resource instances and aren't secret
  # themselves, so declassify just the key set. Each value stays sensitive
  # wherever it's actually used below (secret_string).
  secret_keys = nonsensitive(toset(keys(var.secrets)))
}

resource "aws_secretsmanager_secret" "this" {
  for_each                = local.secret_keys
  name                    = "${var.prefix}/${each.key}"
  description             = "CommerceOS ${var.prefix} — ${each.key}"
  recovery_window_in_days = var.recovery_window_in_days
  tags                    = var.tags
}

resource "aws_secretsmanager_secret_version" "this" {
  for_each      = local.secret_keys
  secret_id     = aws_secretsmanager_secret.this[each.key].id
  secret_string = var.secrets[each.key]

  lifecycle {
    ignore_changes = [secret_string]
  }
}
