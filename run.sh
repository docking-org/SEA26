#!/bin/bash

set -m
echo $PWD
ls

echo "Starting Redis..."
redis-server --port 6379 --daemonize yes
wait


# Start the FastAPI backend
echo "Starting backend..."
uvicorn backend.app.api:app --host 0.0.0.0 --port 5050 --reload &

# Start the Celery worker
echo "Starting Celery worker..."
celery -A backend.app.celery_worker worker --loglevel=info &

# serve frontend static files
echo "Starting frontend..."
npx serve -s frontend/out -l 3001 &

fg %1

wait