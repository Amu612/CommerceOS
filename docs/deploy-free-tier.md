# CommerceOS — AWS Free Plan Deployment

This guide deploys the complete CommerceOS application on **one EC2
instance** using Docker Compose.

The goal is to keep the architecture as simple and inexpensive as possible
while using the current AWS Free Plan.

> **Important:** AWS Free Plan eligibility, credits, and pricing can change.
> Always verify the current AWS console before creating resources.

## Architecture

```text
GitHub
   │
   │ push to main
   ▼
GitHub Actions
   │
   │ SSH
   ▼
EC2 — Amazon Linux 2023
   │
   ├── Frontend :80
   ├── Backend  :8000
   ├── Worker
   ├── PostgreSQL
   └── Redis

```
