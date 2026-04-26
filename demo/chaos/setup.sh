#!/bin/bash
set -e

# Step 1: Check docker and curl exist
if ! command -v docker &> /dev/null || ! command -v curl &> /dev/null; then
    echo "docker or curl not found"
    exit 1
fi

# Step 2: Check RunbookAI server running on :7000
if ! curl -s http://localhost:7000/incidents &> /dev/null; then
    echo "RunbookAI not running on :7000"
    exit 1
fi

# Step 3: Check SSH keypair exists
KEY_DIR="demo/chaos/keys"
KEY_FILE="$KEY_DIR/demo_key"

if [ ! -f "$KEY_FILE" ]; then
    mkdir -p "$KEY_DIR"
    ssh-keygen -t rsa -N '' -f "$KEY_FILE"
    chmod 600 "$KEY_FILE"
fi

# Step 4: Extract public key and write to authorized_keys
ssh-keygen -y -f "$KEY_FILE" > "$KEY_DIR/authorized_keys"

# Step 5: Check if container 'demo-app' exists and remove if it does
if docker ps -a --format '{{.Names}}' | grep -q '^demo-app$'; then
    docker rm -f demo-app
fi

# Step 6: Build the Docker image
docker build -f demo/chaos/Dockerfile -t runbookai-demo-app .

# Step 7: Run the Docker container
docker run -d --name demo-app -p 2222:22 -p 8080:80 runbookai-demo-app

# Step 8: Sleep for 3 seconds to let SSHD start
sleep 3

# Step 9: Test SSH connection
for i in {1..3}; do
    if ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no -p 2222 root@localhost 'echo READY'; then
        break
    fi
    if [ $i -eq 3 ]; then
        echo "SSH connection failed after 3 tries"
        exit 1
    fi
    sleep 5
done

# Step 10: POST /api/hosts with proper JSON via Python
python3 << 'PYTHON_EOF'
import json
import subprocess

with open('demo/chaos/keys/demo_key', 'r') as f:
    private_key = f.read()

payload = json.dumps({
    'hostname': 'web-01',
    'username': 'root',
    'port': 2222,
    'private_key_pem': private_key
})

subprocess.run([
    'curl', '-X', 'POST', 'http://localhost:7000/api/hosts',
    '-H', 'Content-Type: application/json',
    '-d', payload
])
PYTHON_EOF

# Step 11: Load runbooks from YAML files (parse manually to avoid YAML escaping issues)
python3 << 'PYTHON_EOF'
import json
import subprocess
import os
import re

for filename in os.listdir('demo/chaos/runbooks/'):
    if not filename.endswith('.yaml'):
        continue

    filepath = os.path.join('demo/chaos/runbooks', filename)
    with open(filepath, 'r') as f:
        content = f.read()

    # Manual parsing: extract alert_pattern and content via regex
    alert_pattern_match = re.search(r"alert_pattern:\s*['\"](.+?)['\"]", content)
    content_match = re.search(r"content:\s*['\"](.+?)['\"]", content, re.DOTALL)

    alert_pattern = alert_pattern_match.group(1) if alert_pattern_match else ''
    content_text = content_match.group(1) if content_match else ''

    payload = json.dumps({
        'name': filename.replace('.yaml', ''),
        'alert_pattern': alert_pattern,
        'content': content_text
    })

    subprocess.run([
        'curl', '-X', 'POST', 'http://localhost:7000/runbooks',
        '-H', 'Content-Type: application/json',
        '-d', payload
    ])

print('Runbooks loaded')
PYTHON_EOF

# Step 12: Check .env and remind user to set SUGGEST_MODE=false
if grep -q "SUGGEST_MODE=true" ".env"; then
    echo "WARNING: SUGGEST_MODE=true in .env. For autonomous demo, set SUGGEST_MODE=false and restart server."
fi

echo "Setup complete. Container ready. Run: python demo/chaos/chaos.py"
