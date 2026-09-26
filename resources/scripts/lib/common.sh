# shellcheck shell=bash
# Shared helpers for the resources/scripts service setup tooling. Source, do not execute.

# Directory layout: this file lives in <scripts>/lib/common.sh.
COMMON_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$(cd "${COMMON_SH_DIR}/.." && pwd)"
RESOURCES_DIR="$(cd "${SCRIPTS_DIR}/.." && pwd)"
MANIFEST_PY="${COMMON_SH_DIR}/manifest.py"

log()  { printf '[setup-service] %s\n' "$*" >&2; }
warn() { printf '[setup-service] WARN: %s\n' "$*" >&2; }
die()  { printf '[setup-service] ERROR: %s\n' "$*" >&2; exit 1; }

# --- python / manifest access -------------------------------------------------

PYTHON_BIN=""
find_python() {
  [[ -n "$PYTHON_BIN" ]] && return 0
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      return 0
    fi
  done
  die "python3 is required to read the service manifest but was not found on PATH"
}

manifest() {
  find_python
  "$PYTHON_BIN" "$MANIFEST_PY" "$@"
}

manifest_ids()         { manifest ids; }
manifest_has()         { manifest has "$1"; }
manifest_shellvars()   { manifest shellvars "$1"; }

# --- JDK selection ------------------------------------------------------------

# Major version of the active `java`, normalizing 1.8.0_x -> 8 and 17.0.x -> 17.
java_major() {
  local first ver
  first="$(java -version 2>&1 | head -n1)" || return 1
  ver="${first#*\"}"
  ver="${ver%%\"*}"
  if [[ "$ver" == 1.* ]]; then
    ver="${ver#1.}"
  fi
  printf '%s\n' "${ver%%.*}"
}

# Point JAVA_HOME at the JDK matching $1, preferring an explicit JAVA_<v>_HOME.
# Falls back to the active JDK with a warning on mismatch when no JAVA_<v>_HOME
# is exported for the target version.
select_java() {
  local version="$1"
  local var="JAVA_${version}_HOME"
  local home="${!var:-}"
  if [[ -n "$home" && -x "${home}/bin/java" ]]; then
    export JAVA_HOME="$home"
    export PATH="${home}/bin:${PATH}"
    log "Selected JDK ${version} via ${var}=${home}"
    return 0
  fi
  local current
  current="$(java_major 2>/dev/null || echo unknown)"
  if [[ "$current" != "$version" ]]; then
    warn "Service targets JDK ${version}, but ${var} is unset and the active JDK is ${current}."
    warn "Proceeding with the active JDK; build/run may fail."
  fi
}

# --- artifact resolution ------------------------------------------------------

# Resolve <base>/<glob> to a single absolute path (newest wins on ties).
resolve_artifact() {
  local base="$1" glob="$2"
  [[ -n "$glob" ]] || die "resolve_artifact called with an empty glob"
  local matches=()
  # Save/restore nullglob without `shopt -p` capture: under `set -e`, `shopt -p
  # nullglob` returns non-zero when the option is off and would abort the script.
  local had_nullglob=0
  shopt -q nullglob && had_nullglob=1
  shopt -s nullglob
  # Intentional word-split + glob expansion of a path that contains wildcards.
  # shellcheck disable=SC2206
  matches=( ${base}/${glob} )
  (( had_nullglob )) || shopt -u nullglob
  (( ${#matches[@]} > 0 )) || die "no artifact matched ${base}/${glob} -- did the build run?"
  if (( ${#matches[@]} == 1 )); then
    printf '%s\n' "${matches[0]}"
    return 0
  fi
  # Multiple matches: pick the most recently modified.
  local newest=""
  local candidate
  for candidate in "${matches[@]}"; do
    if [[ -z "$newest" || "$candidate" -nt "$newest" ]]; then
      newest="$candidate"
    fi
  done
  warn "Multiple artifacts matched ${base}/${glob}; using newest: ${newest}"
  printf '%s\n' "$newest"
}

# --- health polling -----------------------------------------------------------

# Build an http URL from host/port/path.
service_url() {
  local host="$1" port="$2" path="$3"
  [[ "$path" == /* ]] || path="/${path}"
  printf 'http://%s:%s%s\n' "$host" "$port" "$path"
}

# Launch a command in its own session (PGID == launched pid) so the whole tree
# can later be signaled, including JVMs/daemons it forks. Writes the pid to
# <pid_file> and stdout/stderr to <log_file>. Used for both pre_run steps and the SUT.
spawn_session() {
  local workdir="$1" command="$2" log_file="$3" pid_file="$4"
  find_python
  "$PYTHON_BIN" -c 'import os,sys; os.chdir(sys.argv[1]); os.setsid(); os.execvp("bash",["bash","-c",sys.argv[2]])' \
    "$workdir" "$command" >"$log_file" 2>&1 </dev/null &
  echo "$!" > "$pid_file"
}

# Poll a TCP host/port until it accepts a connection, or $3 seconds pass. Used for
# datastore readiness (e.g. mongod), which is not HTTP so wait_for_http does not apply.
wait_for_tcp() {
  local host="$1" port="$2" timeout="${3:-60}"
  find_python
  local deadline=$(( $(date +%s) + timeout ))
  log "Waiting up to ${timeout}s for TCP ${host}:${port}"
  while (( $(date +%s) < deadline )); do
    if "$PYTHON_BIN" -c 'import socket,sys; s=socket.socket(); s.settimeout(2); s.connect((sys.argv[1], int(sys.argv[2]))); s.close()' \
        "$host" "$port" 2>/dev/null; then
      log "TCP ${host}:${port} is accepting connections"
      return 0
    fi
    sleep 1
  done
  warn "Timed out after ${timeout}s waiting for TCP ${host}:${port}"
  return 1
}

# Poll $1 until it answers with an HTTP status in [200,499], or $2 seconds pass.
# A 200-499 response means the server is listening and not erroring; 000 (no
# connection) and 5xx are treated as "not ready yet".
wait_for_http() {
  local url="$1" timeout="${2:-180}"
  local deadline=$(( $(date +%s) + timeout ))
  local code
  log "Waiting up to ${timeout}s for ${url}"
  while (( $(date +%s) < deadline )); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null || echo 000)"
    if [[ "$code" =~ ^[2-4][0-9][0-9]$ ]]; then
      log "Healthy: ${url} -> HTTP ${code}"
      return 0
    fi
    sleep 2
  done
  warn "Timed out after ${timeout}s waiting for ${url} (last status: ${code:-000})"
  return 1
}
