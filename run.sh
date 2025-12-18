#!/bin/bash

set -m
echo $PWD
ls

echo "Starting Redis..."
redis-server --port 6380 --daemonize yes
wait

# Start the Celery worker
echo "Starting Celery worker..."
celery -A backend.app.celery_worker worker --loglevel=info --concurrency=20  --max-tasks-per-child=30 &

# Start the FastAPI backend
echo "Starting backend..."
uvicorn backend.app.api:app --host 0.0.0.0 --port 5050 --reload &

fg %1
