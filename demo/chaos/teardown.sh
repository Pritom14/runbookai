#!/bin/bash
set -e

echo 'Cleaning up demo...'

# Kill and remove container
if docker ps -a --format '{{.Names}}' | grep -q '^demo-app$'; then
  docker rm -f demo-app || true
  echo 'Container removed.'
fi

# Remove runbooks (optional)
# curl -X GET http://localhost:7000/runbooks 2>/dev/null | jq '.[] | select(.alert_pattern | contains("demo-app")) | .id' | while read id; do
#   curl -X DELETE http://localhost:7000/runbooks/$id 2>/dev/null || true
# done

# Remove host (optional)
# curl -X DELETE http://localhost:7000/api/hosts/web-01 2>/dev/null || true

echo 'Cleanup complete.'
