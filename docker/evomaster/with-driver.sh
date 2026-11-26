#!/usr/bin/env bash
# Run a command while a target's EvoMaster driver listens on its controller port.
# Usage: with-driver.sh <driver java argfile> <driver log> <command> [argument...]
set -uo pipefail

argfile=$1
log=$2
shift 2

java "@$argfile" >"$log" 2>&1 &
driver=$!

stop_driver() {
    kill "$driver" 2>/dev/null
    wait "$driver" 2>/dev/null
}

# The controller answers here before any service runs; infoSUT needs a running service.
until curl -fsS -o /dev/null -H 'Connection: keep-alive' \
    http://localhost:40100/controller/api/controllerInfo; do
    if ! kill -0 "$driver" 2>/dev/null; then
        echo "the driver exited before listening; see $log" >&2
        exit 1
    fi
    sleep 1
done

"$@"
status=$?
stop_driver
exit "$status"
