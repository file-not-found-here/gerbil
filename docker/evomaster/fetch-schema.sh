#!/usr/bin/env bash
# Start the service through its driver and save the documents its schema paths serve.
# Usage: fetch-schema.sh <path> <file> [<path> <file>...]
set -euo pipefail

controller=http://localhost:40100/controller/api
# The controller refuses requests that would close their connection.
keep_alive='Connection: keep-alive'
curl -fsS -X PUT -H "$keep_alive" -H 'Content-Type: application/json' \
    -d '{"run": true, "resetState": true}' "$controller/runSUT"
base=$(curl -fsS -H "$keep_alive" "$controller/infoSUT" | grep -o '"baseUrlOfSUT":"[^"]*"' | cut -d'"' -f4)
if [ -z "$base" ]; then
    echo "the driver reported no service URL" >&2
    exit 1
fi
while [ "$#" -gt 0 ]; do
    curl -fsS --max-time 120 -o "$2" "$base$1"
    shift 2
done
