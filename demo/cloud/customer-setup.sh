#!/bin/bash
# Customer onboarding script for RunbookAI cloud agent.
#
# Usage:
#   bash customer-setup.sh <api_key>
#
# Example:
#   bash customer-setup.sh sk_abcd1234...
#
# This script:
# 1. Installs the runbookai-agent package
# 2. Configures the agent with your API key
# 3. Creates ~/.runbookai/config.json
# 4. Starts the agent

set -e

if [ $# -eq 0 ]; then
    echo "Usage: $0 <api_key>"
    echo ""
    echo "Example:"
    echo "  $0 sk_abcd1234..."
    exit 1
fi

API_KEY="$1"

# Validate API key format
if [[ ! "$API_KEY" =~ ^sk_ ]]; then
    echo "Error: API key should start with 'sk_'"
    exit 1
fi

echo "RunbookAI Cloud Agent Setup"
echo "============================"
echo ""
echo "Installing runbookai-agent..."

# TODO: Phase 3.2 — pip install runbookai-agent from PyPI
# For now, we'll use the local package
pip install -e /Users/pritommazumdar/Desktop/runbookai 2>&1 | grep -E "(Successfully|already)" || true

echo ""
echo "Configuring agent with your API key..."
echo ""

# Create config directory
mkdir -p ~/.runbookai

# Create config file
cat > ~/.runbookai/config.json <<EOF
{
  "api_key": "$API_KEY",
  "cloud_url": "https://runbookai.cloud",
  "mcp_port": 7777
}
EOF

echo "Configuration saved to ~/.runbookai/config.json"
echo ""
echo "Starting RunbookAI agent..."
echo ""
echo "The agent is now running in your VPC."
echo "It will automatically:"
echo "  - Connect to RunbookAI cloud"
echo "  - Wait for incidents"
echo "  - Execute tools (SSH, metrics, logs) when incidents arrive"
echo ""
echo "Press Ctrl+C to stop the agent"
echo ""

# Start the agent
python -m runbookai.agent.cloud_agent
