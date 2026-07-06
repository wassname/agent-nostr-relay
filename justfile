# Agent Nostr Relay — task runner
# Usage: just <recipe>

# Default relay URLs (override with env vars)
SEARCH_URL := env_var_or_default("SEARCH_URL", "http://127.0.0.1:8888")
RELAY_URL  := env_var_or_default("RELAY_URL", "ws://127.0.0.1:7777")

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

# Initialize terraform (first time only)
tf-init:
    cd terraform && terraform init

# Plan terraform deployment
tf-plan domain="yourdomain.com" subdomain="relay" key_name="agent-relay" env="dev":
    cd terraform && terraform plan -var="domain={{domain}}" -var="subdomain={{subdomain}}" -var="key_name={{key_name}}" -var="environment={{env}}"

# Apply terraform deployment
tf-apply domain="yourdomain.com" subdomain="relay" key_name="agent-relay" env="dev":
    cd terraform && terraform apply -var="domain={{domain}}" -var="subdomain={{subdomain}}" -var="key_name={{key_name}}" -var="environment={{env}}"

# SSH into the deployed EC2 instance (requires terraform output)
ssh:
    ssh -i ~/.aws/agent-relay.pem ubuntu@$$(cd terraform && terraform output -raw instance_public_dns)
