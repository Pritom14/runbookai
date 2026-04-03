#!/bin/bash
# Generate a demo SSH key pair used by docker-compose.
# Run once before `docker compose up`.
# Keys are gitignored — never commit them.

set -e

DIR="$(cd "$(dirname "$0")" && pwd)/ssh_keys"
mkdir -p "$DIR"

if [ -f "$DIR/demo_key" ]; then
  echo "Keys already exist at $DIR/demo_key — skipping."
  exit 0
fi

ssh-keygen -t ed25519 -f "$DIR/demo_key" -N "" -C "runbookai-demo"
echo ""
echo "Demo SSH key pair generated:"
echo "  Private: $DIR/demo_key"
echo "  Public:  $DIR/demo_key.pub"
echo ""
echo "Run: docker compose up"
