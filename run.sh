#!/bin/bash

set -m
ls

echo "Starting Redis..."
redis-server --port 6380 --daemonize yes
wait

# Start the Celery worker
echo "Starting Celery worker..."
# celery -A backend.app.celery_worker worker --beat -s /tmp/sea-celerybeat-schedule --loglevel=info --concurrency=20  --max-tasks-per-child=30 &
celery -A backend.app.celery_worker worker --beat \
  -s /tmp/sea-celerybeat-schedule \
  --loglevel=info \
  --concurrency=72 \
  --pool=prefork \
  --max-tasks-per-child=300 &

# Start the FastAPI backend
echo "Starting backend..."
uvicorn backend.app.api:app --host 0.0.0.0 --port 5050 --reload &

fg %1
