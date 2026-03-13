#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/Users/Naples/YoutubeNotes"
COMMAND="cd ${PROJECT_DIR} && bash tools/run_discovery.sh"

exec /bin/bash "${PROJECT_DIR}/tools/scheduler/run_scheduled_job.sh" \
    --job discovery \
    --project-dir "${PROJECT_DIR}" \
    --tz Asia/Shanghai \
    --command "${COMMAND}"
