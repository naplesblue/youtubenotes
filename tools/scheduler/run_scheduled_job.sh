#!/usr/bin/env bash
set -u -o pipefail

usage() {
    cat <<'EOF'
Usage:
  bash tools/scheduler/run_scheduled_job.sh \
    --job <job_name> \
    --command "<shell command>" \
    [--project-dir <path>] \
    [--tz <timezone>]

Example:
  bash tools/scheduler/run_scheduled_job.sh \
    --job routine \
    --command "cd /Users/Naples/YoutubeNotes && python run_pipeline.py && python run_tracker.py all" \
    --tz Asia/Shanghai
EOF
}

JOB_NAME=""
JOB_COMMAND=""
PROJECT_DIR="/Users/Naples/YoutubeNotes"
TZ_NAME="Asia/Shanghai"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --job)
            JOB_NAME="${2:-}"
            shift 2
            ;;
        --command)
            JOB_COMMAND="${2:-}"
            shift 2
            ;;
        --project-dir)
            PROJECT_DIR="${2:-}"
            shift 2
            ;;
        --tz)
            TZ_NAME="${2:-}"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ -z "$JOB_NAME" || -z "$JOB_COMMAND" ]]; then
    echo "Both --job and --command are required." >&2
    usage >&2
    exit 2
fi

LOG_ROOT="${PROJECT_DIR}/logs/scheduler"
LOCK_ROOT="${PROJECT_DIR}/runtime/locks"
JOB_LOG_DIR="${LOG_ROOT}/${JOB_NAME}"
TODAY="$(TZ="$TZ_NAME" date '+%Y-%m-%d')"
RUN_LOG="${JOB_LOG_DIR}/${TODAY}.log"
RUN_HISTORY="${JOB_LOG_DIR}/runs.jsonl"
LOCK_DIR="${LOCK_ROOT}/${JOB_NAME}.lock"

mkdir -p "$JOB_LOG_DIR" "$LOCK_ROOT"

now_local() {
    TZ="$TZ_NAME" date '+%Y-%m-%d %H:%M:%S %Z'
}

now_iso() {
    TZ="$TZ_NAME" date '+%Y-%m-%dT%H:%M:%S%z'
}

append_history() {
    local status="$1"
    local exit_code="$2"
    local start_iso="$3"
    local end_iso="$4"
    local duration="$5"
    local note="$6"

    printf '{"job":"%s","status":"%s","exit_code":%s,"start_time":"%s","end_time":"%s","duration_seconds":%s,"pid":%s,"host":"%s","log_file":"%s","note":"%s"}\n' \
        "$JOB_NAME" "$status" "$exit_code" "$start_iso" "$end_iso" "$duration" "$$" "$(hostname)" "$RUN_LOG" "$note" >> "$RUN_HISTORY"
}

lock_note=""
lock_pid="unknown"
lock_result=0

if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" > "${LOCK_DIR}/pid"
    printf '%s\n' "$JOB_NAME" > "${LOCK_DIR}/job"
else
    if [[ -f "${LOCK_DIR}/pid" ]]; then
        lock_pid="$(cat "${LOCK_DIR}/pid" 2>/dev/null || echo unknown)"
    fi

    if [[ "$lock_pid" =~ ^[0-9]+$ ]] && kill -0 "$lock_pid" 2>/dev/null; then
        lock_result=1
    else
        rm -rf "$LOCK_DIR"
        if mkdir "$LOCK_DIR" 2>/dev/null; then
            printf '%s\n' "$$" > "${LOCK_DIR}/pid"
            printf '%s\n' "$JOB_NAME" > "${LOCK_DIR}/job"
            lock_note="stale_lock_recovered:${lock_pid}"
        else
            lock_result=2
        fi
    fi
fi

start_epoch="$(date +%s)"
start_iso="$(now_iso)"

if [[ "$lock_result" -eq 1 ]]; then
    end_epoch="$(date +%s)"
    end_iso="$(now_iso)"
    duration="$((end_epoch - start_epoch))"
    lock_note="already_running_pid:${lock_pid}"
    {
        echo "[$(now_local)] [SKIP] job=${JOB_NAME} reason=lock_held pid=${lock_pid}"
    } >> "$RUN_LOG"
    append_history "skipped_locked" "99" "$start_iso" "$end_iso" "$duration" "$lock_note"
    exit 99
fi

if [[ "$lock_result" -eq 2 ]]; then
    end_epoch="$(date +%s)"
    end_iso="$(now_iso)"
    duration="$((end_epoch - start_epoch))"
    {
        echo "[$(now_local)] [ERROR] job=${JOB_NAME} reason=lock_acquire_failed"
    } >> "$RUN_LOG"
    append_history "error" "98" "$start_iso" "$end_iso" "$duration" "lock_acquire_failed"
    exit 98
fi

cleanup_lock() {
    rm -rf "$LOCK_DIR"
}
trap cleanup_lock EXIT

{
    echo "================================================================"
    echo "[$(now_local)] [START] job=${JOB_NAME} pid=$$"
    echo "project_dir=${PROJECT_DIR}"
    echo "command=${JOB_COMMAND}"
    if [[ -n "$lock_note" ]]; then
        echo "note=${lock_note}"
    fi
    echo "----------------------------------------------------------------"
} >> "$RUN_LOG"

set +e
/bin/bash -lc "$JOB_COMMAND" >> "$RUN_LOG" 2>&1
cmd_exit="$?"
set -e

end_epoch="$(date +%s)"
end_iso="$(now_iso)"
duration="$((end_epoch - start_epoch))"
status="success"
if [[ "$cmd_exit" -ne 0 ]]; then
    status="failure"
fi

{
    echo "----------------------------------------------------------------"
    echo "[$(now_local)] [END] job=${JOB_NAME} status=${status} exit_code=${cmd_exit} duration_seconds=${duration}"
    echo "================================================================"
    echo
} >> "$RUN_LOG"

append_history "$status" "$cmd_exit" "$start_iso" "$end_iso" "$duration" "$lock_note"
exit "$cmd_exit"
