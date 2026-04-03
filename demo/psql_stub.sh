#!/bin/bash
# Stub psql that returns realistic demo DB metrics.

cat <<'EOF'
 total_connections | active | idle_in_tx | longest_query_s
-------------------+--------+------------+-----------------
                98 |     87 |         12 |              67
(1 row)

 lock_count
------------
          3
(1 row)
EOF
