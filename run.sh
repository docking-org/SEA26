#!/bin/bash
source /opt/conda/etc/profile.d/conda.sh
conda activate sea25
# Start backend (FastAPI)

cd backend
uvicorn backend.app.api:app --host 0.0.0.0 --port 5050

# Start frontend (Next.js)
# cd frontend
# npm start

# Wait for both processes (optional in prod, remove for Docker CMD)
# wait