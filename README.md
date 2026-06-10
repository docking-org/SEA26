# SEA Web Server

A web application for running [Similarity Ensemble Approach (SEA)](https://sea.bkslab.org/) searches and viewing results. SEA relates proteins based on the set-wise chemical similarity among their ligands and can be used to rapidly search large compound databases and build cross-target similarity maps.

Provided by the [Shoichet Laboratory](https://bkslab.org) in the Department of Pharmaceutical Chemistry at the University of California, San Francisco (UCSF).


## Architecture

| Service | Description |
|---|---|
| **web** | FastAPI backend serving the API and (in production) the built React frontend |
| **redis** | Message broker and result backend for Celery |
| **redisworker** | Celery worker processing SEA scoring tasks |
| **redisbeat** | Celery beat scheduler for periodic tasks |
| **frontend** | Vite dev server with HMR (development only) |

## Prerequisites

- Docker and Docker Compose
- Access to the SEA data folder and job directory (NFS or local paths)

## Environment Setup

Create a `.env` file in the project root:

```env
REDIS_HOST=redis
REDIS_PORT=6380

CELERY_BROKER_URL="redis://redis:6380/0"
CELERY_RESULT_BACKEND="redis://redis:6380/1"
MAX_QUEUE_SIZE=50

SEA_DATA_FOLDER="/path/to/sea_data"
SEA_JOB_DIR="/path/to/sea_jobs"

# SEA parameters
TC_CUTOFF=0.29
MEAN_PARA=4.24 * 10 ** -4
STD_COEF=4.49 * 10 ** -3
STD_EXP=0.665
RADIUS=2
FP_SIZE=2048
USE_FEATUREs=False

# ALTCHA captcha HMAC secret (generate your own)
ALTCHA_HMAC_SECRET="your-secret-here"
```

## Running with Docker

### Development

Uses `Dockerfile.dev` with a separate Vite dev server for frontend HMR, source-mounted backend for live reload, and lower worker concurrency.

```bash
docker compose -f compose.dev.yml up --build
```

- Frontend (Vite HMR): http://localhost:5173
- Backend API: http://localhost:5055

### Production

Uses `Dockerfile.prod` with a multi-stage build that compiles the React frontend and bundles it into the backend image.

```bash
docker compose -f compose.prod.yml up --build -d
```

- Application: http://localhost:5050

## Running without Docker

### 1. Set up the Conda environment

```bash
conda env create -f environment.yml
conda activate sea25
pip install -r requirements.txt
```

### 2. Start Redis

```bash
redis-server --port 6380 --daemonize yes
```

### 3. Start the backend and worker (in separate terminals)

```bash
uvicorn backend.app.api:app --host 0.0.0.0 --port 5050 --reload
```

```bash
celery -A backend.app.celery_worker worker --beat -s /tmp/sea-celerybeat-schedule --loglevel=info --concurrency=40 --max-tasks-per-child=60
```

### 4. Start the frontend dev server

```bash
cd frontend
npm install
npm run dev
```
