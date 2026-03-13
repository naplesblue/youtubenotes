#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/Users/Naples/YoutubeNotes"
COMMAND="cd ${PROJECT_DIR} && python run_pipeline.py && python run_tracker.py all"

exec /bin/bash "${PROJECT_DIR}/tools/scheduler/run_scheduled_job.sh" \
    --job routine \
    --project-dir "${PROJECT_DIR}" \
    --tz Asia/Shanghai \
    --command "${COMMAND}"
