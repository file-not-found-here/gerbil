#!/usr/bin/env bash
# Runs `gerbil execution` over every measured project under nohup, resuming from the results on disk.
#
#   scripts/run_execution.sh bundle    where the outputs are: write the inputs to send, as zips
#   scripts/run_execution.sh check     verify this machine can run
#   scripts/run_execution.sh start [all|coverage|mutation] [PROJECT...]
#   scripts/run_execution.sh status
#   scripts/run_execution.sh stop
#   scripts/run_execution.sh results   gather results and log ends to send back, as zips
#
# Environment: OUT (remembered after start), BUNDLE, RESULTS, PART_MB (largest zip), WORKERS
# (PIT workers at once, over all projects), THREADS (PIT workers of one suite), JOBS (projects
# mutated at once), SAMPLE (mutants sampled per project), PIT_TIMEOUT, RETRIES, COVERAGE_CAP,
# MUTATION_CAP, MIN_FREE_GB (needed to start), PAUSE_BELOW_GB (new suites and PIT runs wait
# while less is free).
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
SELF="$ROOT/scripts/$(basename "${BASH_SOURCE[0]}")"
cd "$ROOT" || exit 1

REMEMBERED_OUT=outputs/.execution-out
if [ -z "${OUT:-}" ] && [ -f "$REMEMBERED_OUT" ]; then
  OUT=$(cat "$REMEMBERED_OUT")
fi
OUT=${OUT:-outputs/execution}
# Projects are mutated side by side and share WORKERS, so JOBS need not hold them back.
JOBS=${JOBS:-9}
THREADS=${THREADS:-6}
WORKERS=${WORKERS:-}
RETRIES=${RETRIES:-1}
REQUESTED_PIT_TIMEOUT=${PIT_TIMEOUT:-}
PIT_TIMEOUT=${PIT_TIMEOUT:-180}
REQUESTED_SAMPLE=${SAMPLE:-}
SAMPLE=${SAMPLE:-2401}
COVERAGE_CAP=${COVERAGE_CAP:-8h}
MUTATION_CAP=${MUTATION_CAP:-10d}
# A run already pinned to a bundle resumes from its own copy unless told otherwise.
if [ -z "${BUNDLE:-}" ] && [ -f "$OUT/.run/bundle" ] && [ -f "$OUT/.bundle/manifest.json" ]; then
  BUNDLE=$OUT/.bundle
fi
BUNDLE=${BUNDLE:-outputs/execution-bundle}
RESULTS=${RESULTS:-outputs/execution-results}
PART_MB=${PART_MB:-15}
MIN_FREE_GB=${MIN_FREE_GB:-40}
PAUSE_BELOW_GB=${PAUSE_BELOW_GB:-10}
# A test JVM or PIT worker may grow to its 16 GiB heap plus the JVM's own memory, and one
# the kernel kills loses its results or, in PIT, counts as a kill, so each reserves this.
JVM_MEMORY_GIB=18

# The smallest first, to show within minutes whether the machine can run; then those whose
# mutation takes longest, which starts once they are covered and so runs beside the others'
# coverage. cbioportal has no EvoMaster suite, so it is not measured.
PROJECTS=(
  openvsx
  spinnaker
  kylin
  DSpace
  uaa
  spring-cloud-dataflow
  dolphinscheduler
  incubator-kie-kogito-runtimes
  hertzbeat
)
SUITES=(developer claude-code codex evomaster)

prepare_out() {
  mkdir -p "$OUT/.run" || exit 1
  OUT_ABS=$(cd "$OUT" && pwd -P)
  RUN_DIR="$OUT_ABS/.run"
  LOG="$OUT_ABS/run.log"
}

note() {
  printf '%s %s\n' "$(date '+%F %T')" "$*" >> "$LOG"
}

# The checkout's commit, marked dirty when tracked files differ, since a pull can change
# the code between projects.
gerbil_revision() {
  local commit
  commit=$(git -C "$ROOT" rev-parse HEAD 2> /dev/null) || commit=unknown
  if [ -n "$(git -C "$ROOT" status --porcelain --untracked-files=no 2> /dev/null)" ]; then
    commit="$commit-dirty"
  fi
  printf '%s' "$commit"
}

timeout_command() {
  command -v timeout || command -v gtimeout
}

# Also finds work whose runner died, so it cannot be doubled by a second start.
active_pids() {
  local out
  out=$(printf '%s' "$OUT_ABS" | sed 's/[][\\.*^$()+?{}|]/\\&/g')
  pgrep -u "$(id -u)" -f "run_execution\.sh _(run|one) $out( |\$)|(uv run |bin/)gerbil execution (coverage|mutation) .*--output-dir $out( |\$)"
}

active_containers() {
  local prefix=$1 id label
  docker ps -q --filter label=gerbil.output | while read -r id; do
    label=$(docker inspect -f '{{index .Config.Labels "gerbil.output"}}' "$id")
    case "$label" in
      "$prefix"*) echo "$id" ;;
    esac
  done
}

# A killed `docker run` client leaves its container running.
kill_containers() {
  local id
  for id in $(active_containers "$1"); do
    docker kill "$id" > /dev/null
  done
}

# Parents go first, so xargs cannot start another project as its child exits.
kill_tree() {
  local pid=$1 children child
  children=$(pgrep -P "$pid")
  kill "$pid" 2> /dev/null
  for child in $children; do
    kill_tree "$child"
  done
}

coverage_finished() {
  local project=$1 suite
  for suite in "${SUITES[@]}"; do
    [ -f "$OUT_ABS/$project/$suite/coverage.json" ] || return 1
  done
}

missing_coverage_suites() {
  local project=$1 suite
  for suite in "${SUITES[@]}"; do
    if [ ! -f "$OUT_ABS/$project/$suite/coverage.json" ]; then
      printf '%s ' "$suite"
    fi
  done
}

pending_mutation_suites() {
  local project=$1 suite
  for suite in "${SUITES[@]}"; do
    if [ ! -f "$OUT_ABS/$project/$suite/mutation.json" ]; then
      printf '%s ' "$suite"
    fi
  done
}

# Checks the bundle against the checkout and repositories, reporting on stderr, and
# prints the id of the inputs it holds.
check_inputs() {
  local report code
  report=$(uv run gerbil execution check --bundle "$BUNDLE" --output-dir "$OUT" \
    --min-free-gb "$MIN_FREE_GB")
  code=$?
  printf '%s\n' "$report" >&2
  [ "$code" -eq 0 ] || return 1
  printf '%s\n' "$report" | sed -n 's/^inputs \([0-9a-f]*\)$/\1/p'
}

execute_stage() {
  local stage=$1 project=$2 cap=$3 code
  shift 3
  rm -f "$RUN_DIR/$project.$stage.failed"
  date '+%F %T' > "$RUN_DIR/$project.$stage.running"
  note "START $stage $project gerbil=$(gerbil_revision) $*"
  "$(timeout_command)" -k 60 "$cap" uv run gerbil execution "$stage" \
    --project "$project" --output-dir "$OUT_ABS" "$@" \
    > "$OUT_ABS/$project-$stage.out" 2>&1
  code=$?
  rm -f "$RUN_DIR/$project.$stage.running"
  if [ "$code" -ne 0 ]; then
    echo "$code" > "$RUN_DIR/$project.$stage.failed"
    kill_containers "$OUT_ABS/$project/"
  fi
  note "END $stage $project exit=$code"
}

run_coverage() {
  local project=$1
  if coverage_finished "$project"; then
    note "SKIP coverage $project (already finished)"
    return 0
  fi
  execute_stage coverage "$project" "$COVERAGE_CAP" --pause-below-gb "$PAUSE_BELOW_GB" \
    --bundle "$BUNDLE"
}

run_mutation() {
  local project=$1 suite pending
  local args=(--threads "$THREADS" --workers "$WORKERS" --timeout "$PIT_TIMEOUT"
    --sample-size "$SAMPLE" --pause-below-gb "$PAUSE_BELOW_GB")
  if ! coverage_finished "$project"; then
    note "SKIP mutation $project (coverage is incomplete)"
    return 0
  fi
  pending=$(pending_mutation_suites "$project")
  if [ -z "$pending" ]; then
    note "SKIP mutation $project (already finished)"
    return 0
  fi
  # Naming suites keeps the finished ones, which a project-wide rerun would delete.
  for suite in $pending; do
    args+=(--suite "$suite")
  done
  execute_stage mutation "$project" "$MUTATION_CAP" "${args[@]}"
}

# Sequential because builds write to the Maven and Gradle caches all projects share. Prints
# each project whose coverage is finished, so its mutation starts while the next is covered.
cover() {
  local project
  for project in "$@"; do
    run_coverage "$project"
    if coverage_finished "$project"; then
      echo "$project"
    fi
  done
  return 0
}

# Mutates the projects named on its input as they arrive, JOBS at a time. GNU xargs runs
# its command once for an empty input unless told not to.
mutate() {
  xargs -r -P "$JOBS" -n 1 "$SELF" _one "$OUT_ABS"
}

run_stage() {
  local stage=$1
  shift
  case "$stage" in
    coverage) cover "$@" > /dev/null ;;
    mutation) printf '%s\n' "$@" | mutate ;;
    all)
      cover "$@" | mutate
      return "${PIPESTATUS[1]}"
      ;;
  esac
}

incomplete_projects() {
  local stage=$1 project
  shift
  for project in "$@"; do
    if ! coverage_finished "$project" \
      || { [ "$stage" != coverage ] && [ -n "$(pending_mutation_suites "$project")" ]; }; then
      printf '%s ' "$project"
    fi
  done
}

run_all() {
  local stage=$1 attempt=0 code
  local incomplete=()
  shift
  rm -f "$RUN_DIR"/*.running
  note "RUN stage=$stage jobs=$JOBS threads=$THREADS workers=$WORKERS sample=$SAMPLE" \
    "gerbil=$(gerbil_revision) projects=$*"
  incomplete=("$@")
  while :; do
    run_stage "$stage" "${incomplete[@]}"
    code=$?
    if [ "$code" -ne 0 ]; then
      note "ABORTED xargs exit=$code; run start again to continue"
      return 0
    fi
    read -r -a incomplete <<< "$(incomplete_projects "$stage" "$@")"
    if [ "${#incomplete[@]}" -eq 0 ] || [ "$attempt" -ge "$RETRIES" ]; then
      break
    fi
    attempt=$((attempt + 1))
    note "RETRY $attempt of $RETRIES: ${incomplete[*]}"
  done
  note "FINISHED incomplete: ${incomplete[*]:-none}"
}

check() {
  local problems=0 path
  for path in docker uv git pgrep; do
    if ! command -v "$path" > /dev/null; then
      echo "missing command: $path"
      problems=1
    fi
  done
  if ! timeout_command > /dev/null; then
    echo "missing command: timeout"
    problems=1
  fi
  if [ "$problems" -ne 0 ]; then
    return 1
  fi
  if ! docker info > /dev/null 2>&1; then
    echo "docker is not usable by this user"
    problems=1
  fi
  if ! INPUTS_ID=$(check_inputs) || [ -z "$INPUTS_ID" ]; then
    problems=1
  fi
  [ "$problems" -eq 0 ] && echo "ready"
  return "$problems"
}

# Docker's memory in GiB and its CPUs.
docker_capacity() {
  local memory cpus
  read -r memory cpus <<< "$(docker info --format '{{.MemTotal}} {{.NCPU}}')"
  case "$memory$cpus" in
    "" | *[!0-9]*)
      echo "cannot read Docker's memory and CPUs: '$memory $cpus'" >&2
      return 2
      ;;
  esac
  echo "$((memory / 1024 / 1024 / 1024)) $cpus"
}

# As many PIT workers as Docker's memory holds beside two more JVMs' worth, for coverage's
# test JVM and for PIT's own processes, and at most one per CPU.
default_workers() {
  local memory cpus workers
  read -r memory cpus <<< "$(docker_capacity)"
  [ -n "$memory" ] || return 2
  workers=$((memory / JVM_MEMORY_GIB - 2))
  if [ "$workers" -gt "$cpus" ]; then
    workers=$cpus
  fi
  if [ "$workers" -lt 1 ]; then
    workers=1
  fi
  echo "$workers"
}

# What a start would run with, to see before a run of days.
report_capacity() {
  local memory cpus
  read -r memory cpus <<< "$(docker_capacity)"
  [ -n "$memory" ] || return 2
  echo "docker: $memory GiB, $cpus CPUs; mutation would run ${WORKERS:-$(default_workers)} PIT workers"
}

# Coverage runs one test JVM at a time; mutation runs up to WORKERS workers, beside
# coverage's JVM when both stages run.
check_docker_memory() {
  local stage=$1 memory cpus jvms need
  read -r memory cpus <<< "$(docker_capacity)"
  [ -n "$memory" ] || return 2
  case "$stage" in
    coverage) jvms=1 ;;
    mutation) jvms=$WORKERS ;;
    *) jvms=$((WORKERS + 1)) ;;
  esac
  need=$((jvms * JVM_MEMORY_GIB))
  if [ "$memory" -lt "$need" ]; then
    echo "$stage needs $need GiB for $jvms JVM(s) of up to $JVM_MEMORY_GIB GiB at once" \
      "(WORKERS=$WORKERS); Docker has $memory GiB" >&2
    if [ "$stage" = all ] && [ "$memory" -ge $((need - JVM_MEMORY_GIB)) ]; then
      echo "starting coverage, then mutation, needs one JVM's memory less" >&2
    fi
    return 2
  fi
}

bundle() {
  uv run gerbil execution bundle --output "$BUNDLE" --part-mb "$PART_MB"
}

results() {
  status > "$OUT_ABS/status.txt"
  uv run gerbil execution results --output-dir "$OUT_ABS" --destination "$RESULTS" \
    --part-mb "$PART_MB"
}

validate_settings() {
  local value
  for value in "$JOBS" "$THREADS" "${WORKERS:-1}" "$SAMPLE" "$PIT_TIMEOUT" "$PART_MB" \
    "$MIN_FREE_GB" "$PAUSE_BELOW_GB"; do
    case "$value" in
      # A leading zero would also make the shell read the number as octal.
      "" | *[!0-9]* | 0*)
        echo "JOBS, THREADS, WORKERS, SAMPLE, PIT_TIMEOUT, PART_MB, MIN_FREE_GB and" \
          "PAUSE_BELOW_GB must be positive integers; got '$value'" >&2
        return 2
        ;;
    esac
  done
  if [ "$PAUSE_BELOW_GB" -gt "$MIN_FREE_GB" ]; then
    echo "PAUSE_BELOW_GB=$PAUSE_BELOW_GB exceeds MIN_FREE_GB=$MIN_FREE_GB: a run could" \
      "start only to wait" >&2
    return 2
  fi
  case "$RETRIES" in
    "" | *[!0-9]* | 0?*)
      echo "RETRIES must be a whole number; got '$RETRIES'" >&2
      return 2
      ;;
  esac
  for value in "$COVERAGE_CAP" "$MUTATION_CAP"; do
    if ! printf '%s' "$value" | grep -Eq '^[0-9]+[smhd]?$'; then
      echo "COVERAGE_CAP and MUTATION_CAP look like 8h or 10d; got '$value'" >&2
      return 2
    fi
  done
}

# Refuses a setting other than the one the results were made with.
check_pin() {
  local pinned="$RUN_DIR/$1" name=$2 requested=$3
  if [ -f "$pinned" ] && [ -n "$requested" ] && [ "$requested" != "$(cat "$pinned")" ]; then
    echo "these results use $name=$(cat "$pinned"); use a new OUT to change it" >&2
    return 2
  fi
}

# PIT counts timeouts as kills, and suites of one project are scored on one sample, so a
# resumed run must change neither the allowance nor the sample size.
check_pins() {
  check_pin pit-timeout PIT_TIMEOUT "$REQUESTED_PIT_TIMEOUT" || return 2
  check_pin sample SAMPLE "$REQUESTED_SAMPLE" || return 2
}

# Fixed by the first start that mutates, since coverage depends on neither.
pin_mutation_settings() {
  [ -f "$RUN_DIR/pit-timeout" ] || echo "$PIT_TIMEOUT" > "$RUN_DIR/pit-timeout"
  [ -f "$RUN_DIR/sample" ] || echo "$SAMPLE" > "$RUN_DIR/sample"
  PIT_TIMEOUT=$(cat "$RUN_DIR/pit-timeout")
  SAMPLE=$(cat "$RUN_DIR/sample")
  export PIT_TIMEOUT SAMPLE
}

# Result files mark finished work, so a resumed run must measure the same inputs. Every
# project reads the copy kept in OUT, which a bundle unpacked later cannot change.
pin_bundle() {
  local pinned="$RUN_DIR/bundle" copy="$OUT_ABS/.bundle"
  if [ ! -f "$pinned" ]; then
    if compgen -G "$OUT_ABS/*/*/coverage.json" > /dev/null; then
      echo "$OUT_ABS holds results no bundle is pinned to; use a new OUT" >&2
      return 2
    fi
    rm -rf "$copy"
  elif [ "$(cat "$pinned")" != "$INPUTS_ID" ]; then
    echo "these results measure another bundle's inputs; use a new OUT for $BUNDLE" >&2
    return 2
  fi
  if [ ! -f "$copy/manifest.json" ]; then
    rm -rf "$copy.partial"
    if ! cp -R "$BUNDLE" "$copy.partial" || ! mv "$copy.partial" "$copy"; then
      echo "cannot copy $BUNDLE into $OUT_ABS" >&2
      return 2
    fi
  fi
  echo "$INPUTS_ID" > "$pinned"
  BUNDLE=$copy
  export BUNDLE
}

start() {
  local stage=${1:-all} project
  local selected=()
  case "$stage" in
    all | coverage | mutation) shift $(($# > 0 ? 1 : 0)) ;;
    *)
      echo "unknown stage: $stage (choose all, coverage or mutation)" >&2
      return 2
      ;;
  esac
  for project in "$@"; do
    case " ${PROJECTS[*]} " in
      *" $project "*) ;;
      *)
        echo "unknown project: $project (choose from ${PROJECTS[*]})" >&2
        return 2
        ;;
    esac
    case " ${selected[*]:-} " in
      *" $project "*) ;;
      *) selected+=("$project") ;;
    esac
  done
  if [ $# -eq 0 ]; then
    selected=("${PROJECTS[@]}")
  fi
  validate_settings || return 2
  check || return $?
  if [ -z "$WORKERS" ]; then
    WORKERS=$(default_workers) || return 2
  fi
  export WORKERS
  check_docker_memory "$stage" || return $?
  prepare_out
  START_LOCK="$RUN_DIR/start.lock"
  if ! mkdir "$START_LOCK" 2> /dev/null; then
    echo "another start is in progress (if not, remove $START_LOCK)" >&2
    return 1
  fi
  trap 'rmdir "$START_LOCK" 2> /dev/null' EXIT
  if [ -n "$(active_pids)" ] || [ -n "$(active_containers "$OUT_ABS/")" ]; then
    echo "already running on $OUT_ABS; use status or stop" >&2
    return 1
  fi
  # Checked before anything is pinned, so a refused start fixes nothing.
  check_pins || return 2
  pin_bundle || return 2
  if [ "$stage" != coverage ]; then
    pin_mutation_settings
  fi
  printf '%s\n' "$OUT_ABS" > "$REMEMBERED_OUT"
  echo "workers=$WORKERS threads=$THREADS jobs=$JOBS sample=$SAMPLE pit_timeout=$PIT_TIMEOUT" \
    > "$RUN_DIR/settings"
  echo "$PAUSE_BELOW_GB" > "$RUN_DIR/pause-below-gb"
  nohup "$SELF" _run "$OUT_ABS" "$stage" "${selected[@]}" >> "$LOG" 2>&1 < /dev/null &
  echo "started as pid $!; it keeps running after you log out"
  echo "results: $OUT_ABS"
  echo "progress: scripts/run_execution.sh status"
}

coverage_state() {
  local project=$1 missing
  if [ -e "$RUN_DIR/$project.coverage.running" ]; then
    if [ -n "$(active_pids)" ]; then
      echo "running since $(cat "$RUN_DIR/$project.coverage.running")"
    else
      echo interrupted
    fi
  elif coverage_finished "$project"; then
    echo done
  elif [ -e "$RUN_DIR/$project.coverage.failed" ]; then
    missing=$(missing_coverage_suites "$project")
    echo "failed: ${missing% }"
  else
    echo pending
  fi
}

mutation_state() {
  local project=$1 suite finished=0
  for suite in "${SUITES[@]}"; do
    [ -f "$OUT_ABS/$project/$suite/mutation.json" ] && finished=$((finished + 1))
  done
  if [ -e "$RUN_DIR/$project.mutation.running" ]; then
    if [ -n "$(active_pids)" ]; then
      echo "running since $(cat "$RUN_DIR/$project.mutation.running"), $finished of ${#SUITES[@]} suites"
    else
      echo "interrupted, $finished of ${#SUITES[@]} suites"
    fi
  elif ! coverage_finished "$project"; then
    echo "waiting for coverage"
  elif [ -z "$(pending_mutation_suites "$project")" ]; then
    echo done
  elif [ -e "$RUN_DIR/$project.mutation.failed" ]; then
    echo "failed (exit $(cat "$RUN_DIR/$project.mutation.failed")), $finished of ${#SUITES[@]} suites"
  else
    echo "pending, $finished of ${#SUITES[@]} suites"
  fi
}

# Counted from the line's end, since a device's name may hold spaces.
free_gb() {
  df -Pk "$OUT_ABS" | awk 'NR == 2 { printf "%d\n", $(NF - 2) * 1024 / 1000000000 }'
}

status() {
  local project free
  echo "results: $OUT_ABS"
  if [ -f "$RUN_DIR/settings" ]; then
    echo "settings: $(cat "$RUN_DIR/settings")"
  fi
  if [ -n "$(active_pids)" ]; then
    echo "running"
  else
    echo "not running"
  fi
  free=$([ -d "$OUT_ABS" ] && free_gb)
  if [ -n "$free" ]; then
    if [ -f "$RUN_DIR/pause-below-gb" ]; then
      PAUSE_BELOW_GB=$(cat "$RUN_DIR/pause-below-gb")
    fi
    if [ "$free" -lt "$PAUSE_BELOW_GB" ]; then
      echo "disk: $free GB free; new suites and PIT runs wait until $PAUSE_BELOW_GB are"
    else
      echo "disk: $free GB free; new suites and PIT runs wait below $PAUSE_BELOW_GB"
    fi
  fi
  printf '%-32s %-40s %s\n' PROJECT COVERAGE MUTATION
  for project in "${PROJECTS[@]}"; do
    printf '%-32s %-40s %s\n' "$project" "$(coverage_state "$project")" "$(mutation_state "$project")"
  done
}

stop() {
  local attempt pid remaining
  # Repeats because a container can start just after a look at `docker ps`.
  for attempt in 1 2 3; do
    for pid in $(active_pids); do
      kill_tree "$pid"
    done
    kill_containers "$OUT_ABS/"
    sleep 1
  done
  remaining="$(active_pids) $(active_containers "$OUT_ABS/")"
  if [ -n "${remaining// /}" ]; then
    echo "still alive: $remaining" >&2
    return 1
  fi
  rm -f "$RUN_DIR"/*.running
  note "STOPPED"
  echo "stopped; start resumes after the finished suites"
}

main() {
  local command=${1:-}
  [ $# -gt 0 ] && shift
  case "$command" in
    bundle) validate_settings && bundle ;;
    check) check && report_capacity ;;
    start) start "$@" ;;
    status)
      prepare_out
      status
      ;;
    stop)
      prepare_out
      stop
      ;;
    results)
      validate_settings || exit 2
      prepare_out
      results
      ;;
    _run)
      OUT=$1
      shift
      prepare_out
      # `start` sets the budget; alone, a run has one suite's workers.
      export WORKERS=${WORKERS:-$THREADS}
      run_all "$@"
      ;;
    _one)
      OUT=$1
      prepare_out
      WORKERS=${WORKERS:-$THREADS}
      if [ $# -ge 2 ]; then
        run_mutation "$2"
      fi
      ;;
    *)
      sed -n '2,14p' "$SELF" | sed 's/^# \{0,1\}//'
      exit 2
      ;;
  esac
}

# One line, so a script replaced while it runs is never read past here.
main "$@"; exit "$?"
