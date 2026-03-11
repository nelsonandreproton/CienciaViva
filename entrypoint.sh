#!/bin/bash
set -e
# Fix ownership of the bind-mounted /data volume (may be root-owned on the host)
chown monitor:monitor /data
# Drop privileges and execute monitor as non-root user
exec gosu monitor python /app/monitor.py "$@"
