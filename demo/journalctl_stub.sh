#!/bin/bash
# Stub journalctl that returns realistic demo logs.
# Supports: journalctl -u <service> -n <lines> --no-pager

SERVICE=""
for arg in "$@"; do
  case "$prev" in
    -u) SERVICE="$arg" ;;
  esac
  prev="$arg"
done

case "$SERVICE" in
  checkout-service|checkout)
    cat <<'EOF'
Apr 03 11:42:01 checkout-service[2341]: INFO  Starting request processing
Apr 03 11:42:02 checkout-service[2341]: WARN  Connection pool: 96/100 connections in use
Apr 03 11:42:04 checkout-service[2341]: ERROR Timeout acquiring connection from pool (waited 3002ms) -- pool exhausted
Apr 03 11:42:04 checkout-service[2341]: ERROR Request failed: connection pool exhausted
Apr 03 11:42:07 checkout-service[2341]: WARN  Connection pool: 98/100 connections in use
Apr 03 11:42:09 checkout-service[2341]: ERROR Timeout acquiring connection from pool (waited 3001ms)
Apr 03 11:42:11 checkout-service[2341]: WARN  Slow query detected: SELECT * FROM orders WHERE customer_id=? took 4821ms
EOF
    ;;
  payment-service|payment)
    cat <<'EOF'
Apr 03 03:14:01 payment-service[1823]: INFO  Processing payment request
Apr 03 03:14:02 payment-service[1823]: INFO  Connecting to payment gateway
Apr 03 03:14:03 payment-service[1823]: ERROR java.lang.OutOfMemoryError: Java heap space
Apr 03 03:14:03 payment-service[1823]: ERROR   at com.payments.service.PaymentProcessor.process(PaymentProcessor.java:142)
Apr 03 03:14:03 payment-service[1823]: FATAL Process killed by OOM killer
Apr 03 03:14:05 systemd[1]: payment-service.service: Main process exited, code=killed, status=9/KILL
Apr 03 03:14:05 systemd[1]: payment-service.service: Failed with result 'oom-kill'.
EOF
    ;;
  *)
    echo "-- No entries --"
    ;;
esac
