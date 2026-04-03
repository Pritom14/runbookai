#!/bin/bash
# Stub systemctl for demo SSH target.
# restart always succeeds; is-active always returns "active".

CMD="$1"
SERVICE="$2"

case "$CMD" in
  restart)
    echo "Restarting $SERVICE..."
    sleep 1
    echo "$SERVICE restarted successfully."
    exit 0
    ;;
  is-active)
    echo "active"
    exit 0
    ;;
  status)
    echo "● $SERVICE - Demo Service"
    echo "   Active: active (running)"
    exit 0
    ;;
  *)
    echo "Demo systemctl: $CMD $SERVICE"
    exit 0
    ;;
esac
