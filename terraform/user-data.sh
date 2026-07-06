#!/bin/bash
# EC2 user-data script — runs on first boot
# Installs Docker, clones the repo, starts the relay

set -e

# Install Docker
apt-get update
apt-get install -y ca-certificates curl gnupg lsb-release git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Allow ubuntu user to use docker
usermod -aG docker ubuntu

# Clone repo
cd /opt
git clone https://github.com/wassname/agent-nostr-relay.git
cd agent-nostr-relay

# Build and start
docker compose up -d --build

echo "Agent relay deployed!"
