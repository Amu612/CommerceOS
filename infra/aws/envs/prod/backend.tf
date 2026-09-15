# See envs/dev/backend.tf for the full explanation — same pattern, separate
# state key so dev and prod never share a state file.
#   terraform init -backend-config=backend.hcl
terraform {
  backend "s3" {
    key     = "commerceos/prod/terraform.tfstate"
    encrypt = true
  }
}
