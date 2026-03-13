#! /bin/bash

# TODO: make 3 docker containers
# 1. app
# 2. redis
# 3. celery worker

docker run -d \
    --name sea-redis \
    --network ocean
    --restart unless-stopped \
    -p 6380:6380 \
    redis:latest \
    redis-server --port 6380 

docker run -d \
    --name sea-celery-worker \
    --network ocean
    -v /mnt/nfs/soft/www/home/apps/sea_2025/jobs:/mnt/nfs/soft/www/home/apps/sea_2025/jobs \
    -v /mnt/nfs/exh/work/khtang/chembl36_1um:/mnt/nfs/exh/work/khtang/chembl36_1um \
    -e SEA_JOB_DIR=/mnt/nfs/soft/www/home/apps/sea_2025/jobs \
    -e SEA_DATA_FOLDER=/mnt/nfs/exh/work/khtang/chembl36_1um \
    sea-celery-worker-image:latest


docker run -d \
    --name sea-backend \
    --network ocean
    --restart unless-stopped \
    -p 5050:5050 \
    -v /mnt/nfs/soft/www/home/apps/sea_2025/jobs:/mnt/nfs/soft/www/home/apps/sea_2025/jobs \
    -v /mnt/nfs/exh/work/khtang/chembl36_1um:/mnt/nfs/exh/work/khtang/chembl36_1um \
    -e SEA_JOB_DIR=/mnt/nfs/soft/www/home/apps/sea_2025/jobs \
    -e SEA_DATA_FOLDER=/mnt/nfs/exh/work/khtang/chembl36_1um \
    sea-backend-image:latest
