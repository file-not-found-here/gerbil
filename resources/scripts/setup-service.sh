#!/usr/bin/env bash
#
# setup-service.sh -- build, launch, and health-gate one of the bundled Java
# HTTP services described in services.json, for REST Assured / JAX-RS testing.
#
# All per-service recipes live in services.json (the single source of truth);
# this driver only orchestrates them. Designed to be reusable both locally
# (across the resources/ submodules) and inside the agent runtime container,
# where the sibling general-agent-eval Docker runner calls `up` to bring a
# service online before the agent starts.
#
# Usage:
#   setup-service.sh list
#   setup-service.sh info  <service>
#   setup-service.sh build <service> [opts]
#   setup-service.sh run   <service> [opts]   # foreground (blocks; good as a container CMD)
#   setup-service.sh up    <service> [opts]   # build + background run + health gate
#   setup-service.sh wait  <service> [opts]
#   setup-service.sh health <service> [opts]
#   setup-service.sh stop  <service> [opts]
#
# Options:
#   --repo PATH      Service source directory (default: $SERVICE_REPO, else
#                    resources/<subdir> if writable, else $PWD). The container
#                    integration must pass the staged repo, e.g. /workspace/input.
#   --port N         HTTP port override (default: service default, or $PORT).
#   --host HOST      Host used for health/base URLs (default: $SERVICE_HOST or localhost).
#   --state-dir DIR  Where pid/log/url are written (default: $GERBIL_SERVICE_STATE_DIR
#                    or $TMPDIR/gerbil-services).
#   --timeout SEC    Health-gate timeout override.
#   --no-build       For `up`: skip the build phase (assume already built).
#   --no-wait        For `up`: start in background but do not health-gate.
#
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SELF_DIR}/lib/common.sh"

COMMAND="${1:-help}"
[[ $# -gt 0 ]] && shift || true

REPO_OVERRIDE=""
PORT_OVERRIDE=""
HOST="${SERVICE_HOST:-localhost}"
STATE_ROOT="${GERBIL_SERVICE_STATE_DIR:-${TMPDIR:-/tmp}/gerbil-services}"
TIMEOUT_OVERRIDE=""
DO_BUILD=1
DO_WAIT=1
SERVICE=""

# Accept both "--opt value" and "--opt=value".
while (( $# )); do
  case "$1" in
    --repo)        REPO_OVERRIDE="${2:?--repo needs a value}"; shift 2;;
    --repo=*)      REPO_OVERRIDE="${1#*=}"; shift;;
    --port)        PORT_OVERRIDE="${2:?--port needs a value}"; shift 2;;
    --port=*)      PORT_OVERRIDE="${1#*=}"; shift;;
    --host)        HOST="${2:?--host needs a value}"; shift 2;;
    --host=*)      HOST="${1#*=}"; shift;;
    --state-dir)   STATE_ROOT="${2:?--state-dir needs a value}"; shift 2;;
    --state-dir=*) STATE_ROOT="${1#*=}"; shift;;
    --timeout)     TIMEOUT_OVERRIDE="${2:?--timeout needs a value}"; shift 2;;
    --timeout=*)   TIMEOUT_OVERRIDE="${1#*=}"; shift;;
    --no-build)    DO_BUILD=0; shift;;
    --no-wait)     DO_WAIT=0; shift;;
    -h|--help)     COMMAND="help"; shift;;
    --*)           die "unknown option: $1";;
    *)             [[ -z "$SERVICE" ]] && { SERVICE="$1"; shift; } || die "unexpected argument: $1";;
  esac
done

resolve_repo() {
  local subdir="$1" candidate
  if [[ -n "$REPO_OVERRIDE" ]]; then
    candidate="$REPO_OVERRIDE"
  elif [[ -n "${SERVICE_REPO:-}" ]]; then
    candidate="$SERVICE_REPO"
  elif [[ -d "${RESOURCES_DIR}/${subdir}" && -w "${RESOURCES_DIR}/${subdir}" ]]; then
    candidate="${RESOURCES_DIR}/${subdir}"
  else
    candidate="$PWD"
  fi
  [[ -d "$candidate" ]] || die "service source directory does not exist: ${candidate}"
  ( cd "$candidate" && pwd )
}

load_service() {
  [[ -n "$SERVICE" ]] || die "this command requires a <service> (try: $(basename "$0") list)"
  manifest_has "$SERVICE" || die "unknown service '${SERVICE}' (try: $(basename "$0") list)"
  eval "$(manifest_shellvars "$SERVICE")"
  PORT="${PORT_OVERRIDE:-$SVC_PORT}"
  TIMEOUT="${TIMEOUT_OVERRIDE:-$SVC_HEALTH_TIMEOUT}"
  REPO="$(resolve_repo "$SVC_SUBDIR")"
  STATE_DIR="${STATE_ROOT}/${SERVICE}"
}

# Export PORT plus any manifest env defaults the caller has not already set.
export_runtime_env() {
  export PORT
  local kv key
  for kv in ${SVC_ENV_DEFAULTS[@]+"${SVC_ENV_DEFAULTS[@]}"}; do
    key="${kv%%=*}"
    if [[ -z "${!key:-}" ]]; then
      export "${kv?}"
      log "Default env ${key}=${kv#*=}"
    fi
  done
}

prepare_artifact() {
  if [[ -n "$SVC_ARTIFACT_GLOB" ]]; then
    ARTIFACT="$(resolve_artifact "$REPO" "$SVC_ARTIFACT_GLOB")"
    export ARTIFACT
    log "Artifact: ${ARTIFACT}"
  fi
}

run_build_steps() {
  log "Building ${SERVICE} (JDK ${SVC_JAVA}) in ${REPO}"
  local step
  for step in "${SVC_BUILD[@]}"; do
    log "build: ${step}"
    ( cd "$REPO" && bash -c "$step" ) || die "build step failed: ${step}"
  done
  log "Build complete for ${SERVICE}"
}

# Start each manifest pre_run step (e.g. mongod for genome-nexus) in its own session,
# then wait for its TCP readiness target before returning. Datastore-agnostic: all
# per-service knowledge lives in services.json.
run_pre_run_steps() {
  local n="${#SVC_PRE_RUN_CMDS[@]}"
  (( n > 0 )) || return 0
  mkdir -p "$STATE_DIR"
  export STATE_DIR PORT
  local i name cmd ready tmo host port log_file pid_file
  for (( i = 0; i < n; i++ )); do
    name="${SVC_PRE_RUN_NAMES[$i]:-pre$i}"
    cmd="${SVC_PRE_RUN_CMDS[$i]}"
    ready="${SVC_PRE_RUN_READY[$i]:-}"
    tmo="${SVC_PRE_RUN_TIMEOUT[$i]:-60}"
    log_file="${STATE_DIR}/${name}.log"
    pid_file="${STATE_DIR}/${name}.pid"
    if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
      log "pre_run[${name}] already running (pid $(cat "$pid_file"))"
      continue
    fi
    log "pre_run[${name}]: ${cmd}"
    spawn_session "$REPO" "$cmd" "$log_file" "$pid_file"
    if [[ -n "$ready" ]]; then
      host="${ready%%:*}"; port="${ready##*:}"
      if ! wait_for_tcp "$host" "$port" "$tmo"; then
        warn "Last 20 lines of ${log_file}:"; tail -n 20 "$log_file" >&2 || true
        stop_state_dir
        die "pre_run step '${name}' did not become ready on ${ready}"
      fi
    fi
  done
}

cmd_build() {
  load_service
  select_java "$SVC_JAVA"
  export_runtime_env
  run_build_steps
}

cmd_run() {
  load_service
  select_java "$SVC_JAVA"
  export_runtime_env
  prepare_artifact
  run_pre_run_steps
  log "Starting ${SERVICE} on port ${PORT} (foreground)"
  cd "$REPO"
  exec bash -c "$SVC_RUN"
}

cmd_up() {
  load_service
  select_java "$SVC_JAVA"
  export_runtime_env
  (( DO_BUILD )) && run_build_steps || log "Skipping build (--no-build)"
  prepare_artifact

  mkdir -p "$STATE_DIR"
  local log_file="${STATE_DIR}/service.log"
  local pid_file="${STATE_DIR}/service.pid"
  if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    die "${SERVICE} already running (pid $(cat "$pid_file")); run '$(basename "$0") stop ${SERVICE}' first"
  fi

  run_pre_run_steps

  log "Starting ${SERVICE} on port ${PORT} (background); logs -> ${log_file}"
  # Launch in its own session (PGID == the recorded PID) so `stop` can signal the
  # whole tree, including JVMs forked by cargo/liberty.
  spawn_session "$REPO" "$SVC_RUN" "$log_file" "$pid_file"
  local pid; pid="$(cat "$pid_file")"

  local base_url health_url
  base_url="$(service_url "$HOST" "$PORT" "$SVC_BASE_PATH")"
  health_url="$(service_url "$HOST" "$PORT" "$SVC_HEALTH_PATH")"
  echo "$base_url" > "${STATE_DIR}/service.url"
  echo "$PORT" > "${STATE_DIR}/port"

  if (( DO_WAIT )); then
    if ! wait_for_http "$health_url" "$TIMEOUT"; then
      warn "Last 40 log lines from ${log_file}:"
      tail -n 40 "$log_file" >&2 || true
      stop_state_dir
      die "${SERVICE} did not become healthy within ${TIMEOUT}s"
    fi
  fi
  log "${SERVICE} is up (pid ${pid}). Base URL: ${base_url}"
  printf '%s\n' "$base_url"
}

cmd_stop_pidfile() {
  local pid_file="$1"
  [[ -f "$pid_file" ]] || { warn "no pid file at ${pid_file}; nothing to stop"; return 0; }
  local pid; pid="$(cat "$pid_file")"
  if kill -0 "$pid" 2>/dev/null; then
    log "Stopping process group ${pid}"
    kill -TERM "-${pid}" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    local i
    for i in $(seq 1 10); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    if kill -0 "$pid" 2>/dev/null; then
      warn "force-killing process group ${pid}"
      kill -KILL "-${pid}" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
    fi
  else
    log "process ${pid} not running (stale pid file)"
  fi
  rm -f "$pid_file"
}

# Stop every recorded process for this service (the SUT plus any pre_run daemons).
stop_state_dir() {
  local pf had_nullglob=0
  # See resolve_artifact: avoid `shopt -p` capture so `set -e` survives nullglob being off.
  shopt -q nullglob && had_nullglob=1
  shopt -s nullglob
  for pf in "${STATE_DIR}"/*.pid; do
    cmd_stop_pidfile "$pf"
  done
  (( had_nullglob )) || shopt -u nullglob
}

cmd_stop() {
  load_service
  stop_state_dir
}

cmd_wait() {
  load_service
  wait_for_http "$(service_url "$HOST" "$PORT" "$SVC_HEALTH_PATH")" "$TIMEOUT"
}

cmd_health() {
  load_service
  local url code
  url="$(service_url "$HOST" "$PORT" "$SVC_HEALTH_PATH")"
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null || echo 000)"
  if [[ "$code" =~ ^[2-4][0-9][0-9]$ ]]; then
    log "Healthy: ${url} -> HTTP ${code}"
    return 0
  fi
  warn "Unhealthy: ${url} -> HTTP ${code}"
  return 1
}

cmd_info() {
  load_service
  local deps="${SVC_EXTERNAL[*]:-}"
  printf 'service:        %s\n' "$SVC_ID"
  printf 'description:    %s\n' "$SVC_DESC"
  printf 'repo_subdir:    %s\n' "$SVC_SUBDIR"
  printf 'java_version:   %s\n' "$SVC_JAVA"
  printf 'rest_style:     %s\n' "$SVC_REST_STYLE"
  printf 'default_port:   %s\n' "$SVC_PORT"
  printf 'base_url:       %s\n' "$(service_url "$HOST" "$PORT" "$SVC_BASE_PATH")"
  printf 'health_url:     %s\n' "$(service_url "$HOST" "$PORT" "$SVC_HEALTH_PATH")"
  printf 'external_deps:  %s\n' "${deps:-(none)}"
  printf 'notes:          %s\n' "$(manifest field "$SERVICE" notes)"
}

cmd_list() {
  local id desc
  while IFS= read -r id; do
    [[ -n "$id" ]] || continue
    desc="$(manifest field "$id" description)"
    printf '%-20s %s\n' "$id" "$desc"
  done < <(manifest_ids)
}

usage() {
  # Print the header comment block (from line 3) until the first non-comment line.
  awk 'NR>=3 { if ($0 !~ /^#/) exit; sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}"
}

case "$COMMAND" in
  list)   cmd_list;;
  info)   cmd_info;;
  build)  cmd_build;;
  run)    cmd_run;;
  up)     cmd_up;;
  wait)   cmd_wait;;
  health) cmd_health;;
  stop)   cmd_stop;;
  help|-h|--help) usage;;
  *) die "unknown command '${COMMAND}' (try: $(basename "$0") help)";;
esac
