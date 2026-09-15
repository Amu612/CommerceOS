# Bootstrap — one-time, run by hand

Creates the two things every other Terraform stack (and GitHub Actions) needs
to exist *before* they can run: the remote-state backend, and the OIDC trust
that lets Actions assume an AWS role without a long-lived access key.

This is the only Terraform in the repo that does **not** use a remote
backend — it's what creates the remote backend. State stays local
(`terraform.tfstate` in this directory, gitignored). Run it once per AWS
account; re-running `apply` later is safe and idempotent.

## Prerequisites

- An AWS account + credentials with enough privilege to create IAM roles, an
  OIDC provider, an S3 bucket, and a DynamoDB table (an account admin, once).
- Terraform ≥ 1.7.

## Run it

```bash
cd infra/aws/bootstrap
terraform init
terraform apply
```

## Wire up the rest

1. **Remote state** — for each of `envs/dev` and `envs/prod`:
   ```bash
   terraform output -raw backend_hcl > ../envs/dev/backend.hcl
   terraform output -raw backend_hcl > ../envs/prod/backend.hcl
   ```
   (Or copy `backend.hcl.example` in each and paste the values by hand.)

2. **GitHub Actions secrets** (repo Settings → Secrets and variables →
   Actions), from `terraform output`:
   - `AWS_DEPLOY_ROLE_ARN` = `deploy_role_arn` — used by `.github/workflows/deploy.yml`
   - `AWS_TERRAFORM_ROLE_ARN` = `terraform_role_arn` — used by `.github/workflows/infra.yml`

3. **GitHub Actions variables** (same page, "Variables" tab):
   - `API_BASE_URL`, `WS_BASE_URL`, `PUBLIC_URL` — filled in once
     `envs/dev` has been applied at least once (see its `public_url` output).
     Until then, deploy.yml's smoke test / frontend build args are blank and
     it skips them.

4. **GitHub environments** (repo Settings → Environments): create `dev` and
   `prod`. Add a required reviewer on `prod` — that's what makes a `v*` tag
   push pause for manual approval before it touches production.

If the repo is ever renamed or forked, update `local.github_repo` in
`main.tf` and re-apply — the trust policy is pinned to
`Amu612/CommerceOS` by name.
