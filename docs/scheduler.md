# Scheduler Execution Plan (OpenClaw Friendly)

This document defines the repository-local execution wrappers for daily routine and weekly discovery tasks. It is designed for OpenClaw cron integration and does not modify system `crontab`.

## 1) Entry scripts

- Daily routine entry: `tools/scheduler/run_routine_job.sh`
- Weekly discovery entry: `tools/scheduler/run_discovery_job.sh`
- Shared execution wrapper: `tools/scheduler/run_scheduled_job.sh`
- OpenClaw cron template: `tools/scheduler/openclaw.cron.example`

## 2) Effective commands

- Routine command (2 times/day):
  - `cd /Users/Naples/YoutubeNotes && python run_pipeline.py && python run_tracker.py all`
- Discovery command (Tue/Sat 14:00):
  - `cd /Users/Naples/YoutubeNotes && bash tools/run_discovery.sh`

## 3) Schedule

- Routine:
  - `09:30` Asia/Shanghai
  - `21:30` Asia/Shanghai
- Discovery:
  - Tuesday `14:00` Asia/Shanghai
  - Saturday `14:00` Asia/Shanghai

## 4) Logging, exit code, timing, anti-reentry

`tools/scheduler/run_scheduled_job.sh` handles:

- Start/end timestamps (local timezone and ISO timestamp)
- Exit code pass-through (script returns underlying command exit code)
- Runtime duration in seconds
- Daily run log:
  - `logs/scheduler/<job>/<YYYY-MM-DD>.log`
- Run history JSONL:
  - `logs/scheduler/<job>/runs.jsonl`
- Anti-reentry lock:
  - `runtime/locks/<job>.lock`
  - If a live lock is found, current run exits `99` and logs `skipped_locked`
  - If stale lock is detected, wrapper recovers and continues

## 5) OpenClaw cron integration

Use `tools/scheduler/openclaw.cron.example` as source. Typical entries:

```cron
CRON_TZ=Asia/Shanghai
30 9,21 * * * /bin/bash /Users/Naples/YoutubeNotes/tools/scheduler/run_routine_job.sh
0 14 * * 2,6 /bin/bash /Users/Naples/YoutubeNotes/tools/scheduler/run_discovery_job.sh
```

## 6) Manual trigger

```bash
bash tools/scheduler/run_routine_job.sh
bash tools/scheduler/run_discovery_job.sh
```

## 7) Operational notes and risks

- Python path/environment:
  - Cron/OpenClaw often has a minimal `PATH`.
  - If needed, prepend virtualenv activation in the job wrapper.
- Job overlap:
  - Same job name is protected by lock.
  - Different jobs (`routine`, `discovery`) can run concurrently by design.
- Lock recovery:
  - Wrapper clears stale lock automatically when previous PID is not alive.
- Log growth:
  - Logs are append-only; set external retention/rotation policy if needed.
