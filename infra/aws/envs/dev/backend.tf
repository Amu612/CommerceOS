# Remote state — S3 + DynamoDB lock. The bucket/table are created once by
# `infra/aws/bootstrap` (see its README); this block only *uses* them.
#
# Partial configuration on purpose: `terraform init` reads the bucket/table
# names from `backend.hcl` (copy `backend.hcl.example`, gitignored — the
# names aren't secret, they're just account-specific) so the same code works
# for any AWS account without editing tracked files:
#
#   terraform init -backend-config=backend.hcl
terraform {
  backend "s3" {
    key     = "commerceos/dev/terraform.tfstate"
    encrypt = true
  }
}
