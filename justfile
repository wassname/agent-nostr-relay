# Agent Nostr Relay — task runner
# Usage: just <recipe>

# Default relay URLs (override with env vars)
SEARCH_URL := env_var_or_default("SEARCH_URL", "http://127.0.0.1:8888")
RELAY_URL  := env_var_or_default("RELAY_URL", "ws://127.0.0.1:7777")
AWS_PROFILE := env_var_or_default("AWS_PROFILE", "cds-login")
AWS_REGION := env_var_or_default("AWS_REGION", "us-east-1")
LIVE_INSTANCE_ID := env_var_or_default("LIVE_INSTANCE_ID", "i-0d0b588f2940cb931")
LIVE_INSTANCE_AZ := env_var_or_default("LIVE_INSTANCE_AZ", "us-east-1c")
LIVE_INSTANCE_IP := env_var_or_default("LIVE_INSTANCE_IP", "34.195.99.79")
LIVE_SSH_USER := env_var_or_default("LIVE_SSH_USER", "ubuntu")
LIVE_SSH_KEY := env_var_or_default("LIVE_SSH_KEY", env_var("HOME") + "/.ssh/id_ed25519")

# Smoke test: publish event with PoW, search it, check feed
test:
    #!/bin/bash
    set -e
    echo "=== Agent Relay Smoke Test ==="
    echo "Search: {{SEARCH_URL}}"
    echo "Relay:  {{RELAY_URL}}"
    echo ""

    echo "[1/5] Health check..."
    HEALTH=$(curl -s "{{SEARCH_URL}}/health")
    echo "  $HEALTH"
    echo ""

    echo "[2/5] Publishing test event with PoW..."
    PUB_RESULT=$(python3 -c "
    import json, time, hashlib, websocket
    from pynostr.key import PrivateKey
    from pynostr.event import Event

    sk = PrivateKey()
    ws = websocket.create_connection('{{RELAY_URL}}', timeout=30)

    ev = Event(
        kind=1,
        content='## Smoke test\n\nTesting the agent relay. Searching for **alignment** and **steering**.\n\n#test',
        created_at=int(time.time()),
    )
    ev.pubkey = sk.public_key.hex()
    for nonce in range(10000000):
        ev.tags = [['nonce', str(nonce), '16']]
        serial = ev.serialize()
        eid = hashlib.sha256(serial).hexdigest()
        h = bytes.fromhex(eid)
        bits = 0
        for byte in h:
            if byte == 0: bits += 8
            else: bits += 8 - byte.bit_length(); break
        if bits >= 16:
            ev.id = eid
            ev.sign(sk.hex())
            break
    ws.send(json.dumps(['EVENT', ev.to_dict()]))
    result = ws.recv()
    ws.close()
    print(result)
    ")
    echo "  $PUB_RESULT"
    echo ""

    echo "[3/5] Waiting 2s for indexing..."
    sleep 2
    echo ""

    echo "[4/5] Searching for 'alignment'..."
    SEARCH_HTML=$(curl -s "{{SEARCH_URL}}/search?q=alignment")
    echo "  HTML length: $(echo "$SEARCH_HTML" | wc -c)"
    echo "  Contains 'alignment': $(echo "$SEARCH_HTML" | grep -qi alignment && echo YES || echo NO)"
    echo "  Contains 'Smoke test': $(echo "$SEARCH_HTML" | grep -qi 'smoke test' && echo YES || echo NO)"
    echo ""

    echo "[5/5] Checking feed..."
    FEED_HTML=$(curl -s "{{SEARCH_URL}}/")
    echo "  HTML length: $(echo "$FEED_HTML" | wc -c)"
    echo "  Contains 'Smoke test': $(echo "$FEED_HTML" | grep -qi 'smoke test' && echo YES || echo NO)"
    echo ""

    if echo "$SEARCH_HTML" | grep -qi 'smoke test'; then
        echo "✅ PASS: event published, indexed, searchable, and visible in feed"
    else
        echo "❌ FAIL: event not found in search results"
        exit 1
    fi

# Quick health check
health:
    curl -s "{{SEARCH_URL}}/health" | python3 -m json.tool

# Build and run all services with Docker Compose
up:
    docker compose up -d --build

# Stop all services
down:
    docker compose down

# View logs for all services
logs:
    docker compose logs -f

# View search service logs only
logs-search:
    docker compose logs -f search

# Deploy the live EC2 box. This is the current safe path: EIC SSH, git pull, docker compose.
deploy-live:
    #!/bin/bash
    set -euo pipefail
    aws sts get-caller-identity --profile "{{AWS_PROFILE}}" --region "{{AWS_REGION}}" >/dev/null
    aws ec2-instance-connect send-ssh-public-key \
      --profile "{{AWS_PROFILE}}" \
      --region "{{AWS_REGION}}" \
      --instance-id "{{LIVE_INSTANCE_ID}}" \
      --availability-zone "{{LIVE_INSTANCE_AZ}}" \
      --instance-os-user "{{LIVE_SSH_USER}}" \
      --ssh-public-key "file://{{LIVE_SSH_KEY}}.pub" >/dev/null
    SSH_AUTH_SOCK=none ssh -p 22 -o IdentitiesOnly=yes -i "{{LIVE_SSH_KEY}}" \
      -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
      "{{LIVE_SSH_USER}}@{{LIVE_INSTANCE_IP}}" \
      'set -euo pipefail; cd /opt/agent-nostr-relay; sudo git fetch origin main; sudo git reset --hard origin/main; echo deployed=$(sudo git rev-parse --short HEAD); sudo docker compose up -d --build; sudo docker compose ps'
    curl -fsS https://therustyclaw.com/health
    curl -fsS https://therustyclaw.com/skill.md | head -18

# SSH to the live EC2 box via EC2 Instance Connect.
ssh-live:
    aws ec2-instance-connect send-ssh-public-key --profile "{{AWS_PROFILE}}" --region "{{AWS_REGION}}" --instance-id "{{LIVE_INSTANCE_ID}}" --availability-zone "{{LIVE_INSTANCE_AZ}}" --instance-os-user "{{LIVE_SSH_USER}}" --ssh-public-key "file://{{LIVE_SSH_KEY}}.pub" >/dev/null
    SSH_AUTH_SOCK=none ssh -p 22 -o IdentitiesOnly=yes -i "{{LIVE_SSH_KEY}}" -o StrictHostKeyChecking=accept-new "{{LIVE_SSH_USER}}@{{LIVE_INSTANCE_IP}}"

# Initialize OpenTofu. Use for infra changes, not routine code deploy.
tf-init:
    cd terraform && tofu init

# Import the current live EC2 + security group into local OpenTofu state.
tf-import-live:
    #!/bin/bash
    set -euo pipefail
    cd terraform
    tofu init -input=false
    eval "$(aws configure export-credentials --profile '{{AWS_PROFILE}}' --region '{{AWS_REGION}}' --format env)"
    export AWS_REGION='{{AWS_REGION}}'
    tofu import -input=false aws_security_group.relay sg-03d7cc8ba0437e4b5 || true
    tofu import -input=false aws_instance.relay '{{LIVE_INSTANCE_ID}}' || true
    tofu plan -input=false

# Plan live infrastructure. UAT is "No changes".
tf-plan-live:
    #!/bin/bash
    set -euo pipefail
    cd terraform
    eval "$(aws configure export-credentials --profile '{{AWS_PROFILE}}' --region '{{AWS_REGION}}' --format env)"
    export AWS_REGION='{{AWS_REGION}}'
    tofu plan -input=false

# Apply live infrastructure changes only after tf-plan-live is reviewed.
tf-apply-live:
    #!/bin/bash
    set -euo pipefail
    cd terraform
    eval "$(aws configure export-credentials --profile '{{AWS_PROFILE}}' --region '{{AWS_REGION}}' --format env)"
    export AWS_REGION='{{AWS_REGION}}'
    tofu apply

# Legacy aliases.
tf-plan:
    just tf-plan-live

tf-apply:
    just tf-apply-live

# SSH into terraform-managed EC2 (requires terraform state). For live, use ssh-live.
ssh:
    ssh -i ~/.aws/agent-relay.pem ubuntu@$$(cd terraform && terraform output -raw instance_public_dns)
