#!/bin/bash

set -m
ls

echo "Starting Redis..."
redis-server --port 6380 --daemonize yes
wait

# Start the Celery worker
echo "Starting Celery worker..."
celery -A backend.app.celery_worker worker -n seaworker --beat -s /tmp/sea-celerybeat-schedule --loglevel=info --concurrency=20  --max-tasks-per-child=30 &

# Start the dedicated Boltz worker (concurrency=1 enforces one job at a time)
# User queues (boltz-gpu{N}) are listed before auto queues (boltz-gpu{N}-auto) so
# user-submitted jobs are always picked up before auto-generated jobs.
echo "Starting Boltz worker..."
_N=${BOLTZ_NUM_GPUS:-4}
_USER_QS=$(seq 0 $((_N-1)) | sed 's/.*/boltz-gpu&/' | paste -sd,)
_AUTO_QS=$(seq 0 $((_N-1)) | sed 's/.*/boltz-gpu&-auto/' | paste -sd,)
celery -A backend.app.celery_worker worker -n boltzworker -Q ${_USER_QS},${_AUTO_QS} --concurrency=1 --loglevel=info &

# Start the FastAPI backend
echo "Starting backend..."
uvicorn backend.app.api:app --host 0.0.0.0 --port 5050 --reload &

fg %1
