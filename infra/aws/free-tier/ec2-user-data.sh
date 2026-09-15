#!/bin/bash
# EC2 launch script (paste into "User data" when launching the instance —
# see docs/deploy-free-tier.md). Amazon Linux 2023. Runs once, at first boot.
#
# Installs Docker + the Compose plugin + git, and clones the repo. It does
# NOT start the app — that's done once by hand (first-time setup) and after
# that by the deploy-free-tier.yml GitHub Actions workflow over SSH.
set -euxo pipefail

dnf update -y
dnf install -y docker git

systemctl enable --now docker
usermod -aG docker ec2-user

# Docker Compose v2 plugin (Amazon Linux 2023's docker package doesn't bundle it)
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# CommerceOS repository used for the AWS Free Plan deployment.
# Keep the repository public for this bootstrap method.
# Do not put GitHub personal access tokens or other credentials in user-data.
REPO_URL="https://github.com/Amu612/CommerceOS.git"

sudo -u ec2-user git clone "$REPO_URL" /home/ec2-user/CommerceOS
