# CommerceOS — AWS infrastructure (Terraform)

Provisions the full stack described in `docs/architecture.md` and ADR 0001.

```
infra/aws/
├── modules/
│   ├── network/        VPC, subnets, NAT, VPC endpoints, flow logs
│   ├── ecr/            container registries
│   ├── secrets/        Secrets Manager entries
│   ├── rds/            PostgreSQL (Multi-AZ optional) + read replica + RO role bootstrap
│   ├── redis/          ElastiCache Redis (encrypted, auth token)
│   ├── alb/            ALB, target groups, HTTPS listener, WAF association
│   ├── ecs/            cluster, api service (autoscaled), worker service, migrate task
│   ├── frontend_cdn/   S3 + CloudFront (OAC) + WAF
│   ├── observability/  log groups, dashboard, alarms, SNS
│   └── iam/            task exec + task roles (least privilege)
└── envs/
    ├── dev/            single-AZ, smaller, docs=on
    └── prod/           Multi-AZ, replica, docs=off
```

## Prerequisites
- Terraform ≥ 1.7, AWS CLI configured, an ACM cert for your domain (in the ALB
  region **and** us-east-1 for CloudFront), a Route53 hosted zone.
- Remote state bucket + DynamoDB lock table (see `envs/*/backend.tf`).
- The Olist + DataCo CSVs uploaded to the `datasets` S3 bucket (module `frontend_cdn`
  output `datasets_bucket`) so the `migrate` task can build the warehouse.

## Usage
```bash
cd infra/aws/envs/dev
cp terraform.tfvars.example terraform.tfvars   # fill in domain, cert ARNs, image tags
terraform init
terraform apply
```

## Deploy flow (CI does this)
1. Build + push images to ECR (`commerceos/api`, `commerceos/frontend`).
2. `terraform apply -var image_tag=<sha>` (or update the ECS service directly).
3. Run the one-shot `migrate` task (`alembic upgrade head` + warehouse build + seed users).
4. `aws ecs update-service --force-new-deployment` for `api` and `worker`.
5. Smoke test the public URL; roll back the task def on failure.

See `docs/runbook.md`.
