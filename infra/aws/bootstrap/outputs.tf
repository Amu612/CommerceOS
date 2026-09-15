output "tfstate_bucket" {
  value = aws_s3_bucket.tfstate.bucket
}

output "tfstate_lock_table" {
  value = aws_dynamodb_table.tfstate_lock.name
}

output "deploy_role_arn" {
  description = "-> GitHub secret AWS_DEPLOY_ROLE_ARN"
  value       = aws_iam_role.deploy.arn
}

output "terraform_role_arn" {
  description = "-> GitHub secret AWS_TERRAFORM_ROLE_ARN"
  value       = aws_iam_role.terraform.arn
}

output "backend_hcl" {
  description = "Paste into envs/{dev,prod}/backend.hcl"
  value       = <<-EOT
    bucket         = "${aws_s3_bucket.tfstate.bucket}"
    region         = "${var.region}"
    dynamodb_table = "${aws_dynamodb_table.tfstate_lock.name}"
  EOT
}
