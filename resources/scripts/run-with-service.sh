#!/usr/bin/env bash
#
# run-with-service.sh -- bring a bundled Java service online, then exec the agent.
#
# Used by the sibling general-agent-eval Docker runner as the container command
# when --service is set: it builds and starts <service> (health-gated, in the
# background via setup-service.sh up) and then exec's the agent command, so the
# agent runs against a live API. exec preserves the agent's exit code as the
# container exit code. The service (and any pre_run dependency it needs, e.g.
# genome-nexus's mongod) runs in its own session, so it survives this exec and
# lives for the agent's lifetime; with `docker run --rm` it is reaped when the
# container exits.
#
# Usage:
#   run-with-service.sh <service> [setup-service up options...] -- <agent command...>
#
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SERVICE="${1:?run-with-service.sh: missing <service>}"
shift

# Everything up to `--` is forwarded to `setup-service.sh up`; everything after is the
# agent command to exec.
setup_opts=()
while (( $# )); do
  if [[ "$1" == "--" ]]; then shift; break; fi
  setup_opts+=("$1"); shift
done

(( $# > 0 )) || { echo "run-with-service.sh: missing agent command after --" >&2; exit 2; }

"${SELF_DIR}/setup-service.sh" up "$SERVICE" ${setup_opts[@]+"${setup_opts[@]}"}

exec "$@"
