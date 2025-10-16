#!/bin/bash

# Start backend (FastAPI)
uvicorn backend.app.api:app --host 0.0.0.0 --port 5050 &

# Start frontend (Next.js)
cd frontend
npm run build
npm start

# Wait for both processes (optional in prod, remove for Docker CMD)
# wait