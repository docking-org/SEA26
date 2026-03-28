import os
import sys
import shutil
import uuid
import json
import pandas as pd
from typing import Union
from datetime import datetime
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, APIRouter
from fastapi import Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from celery.utils.log import get_task_logger
from dotenv import load_dotenv, find_dotenv

# import sea analysis functions
import numpy as np
import uuid, asyncio
from .celery_worker import celery
from celery import group, chord
from celery.result import AsyncResult, GroupResult
import pickle
from .celery_worker import _mkdir_, remove_salt, sea_target_score, sea_target_score_batch, warm_target_cache, postprocess_sea_job, redis_cache
from .helper import get_smiles_property, known_test_tc, tsv2json, init_progress_file, save_progress, get_progress

env_path = os.path.join(os.path.dirname(__file__), "../../.env")
load_dotenv(find_dotenv())

# Cache the list of target files at startup
_TARGET_FILES_CACHE = None

# load target files into cache at startup


def get_target_files():
    """Cache and return list of target files."""
    global _TARGET_FILES_CACHE
    if (_TARGET_FILES_CACHE is None):
        data_folder = os.getenv("SEA_DATA_FOLDER")
        _TARGET_FILES_CACHE = []
        for folder in os.listdir(data_folder):
            if not os.path.isdir(os.path.join(data_folder, folder)):
                continue
            for f in os.listdir(os.path.join(data_folder, folder)):
                full_file_name = os.path.join(data_folder, folder, f)
                if os.path.isfile(full_file_name) and full_file_name.endswith(".tsv"): 
                    _TARGET_FILES_CACHE.append(full_file_name)
                else:
                    continue
    return _TARGET_FILES_CACHE

# Pre-load target files on import
get_target_files()



router = APIRouter(prefix="/api")
app = FastAPI()

# Allow requests from your frontend origin
origins = [
    "http://localhost:3001",
    "http://127.0.0.1:3001",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

task_logger = get_task_logger(__name__)


@router.get("" or "/")
def read_root() -> dict[str, Union[str, int]]:
    return {"msg": "Welcome to SEA", "version": 1}

@router.post("/submit")
async def submit_task(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
    input_data = await request.json()
    task_id = str(uuid.uuid4())
    job_dir = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/sea_output"
    _mkdir_(job_dir)
    init_progress_file(task_id)
    # time this function to simulate a long-running task
    background_tasks.add_task(_submit_sea_job, task_id, input_data["query"])
    return {"task_id": task_id}


# sea background task submission using Celery
async def _submit_sea_job(task_id: str, input_data: dict):
    # setup job directory
    import time
    start = time.time()
    try:
        # job_dir = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/sea_output"
        # _mkdir_(job_dir)
        smiles_test = [
            f"{remove_salt(x.strip().split()[0])} {x.strip().split()[1]}"
            for x in input_data.splitlines()
            if x.strip()
        ]
        with open(f"{os.getenv('SEA_JOB_DIR')}/{task_id}/input.smi", "w") as f:
            f.write(input_data)
        # init_progress_file(task_id)
        list_chembl = _TARGET_FILES_CACHE
        total = len(list_chembl)
        save_progress(task_id, {
                            "status": "PENDING",
                            "progress": 0, 
                            "total_targets": total
                            })
        # Store input in Redis once (instead of serializing into every task)
        smiles_key = f"input:{task_id}"
        redis_cache.setex(smiles_key, 3600, pickle.dumps(smiles_test))

        # Chunk targets into batches to reduce broker round-trips
        # 5821 targets / 100 = ~59 tasks instead of 5821
        CHUNK_SIZE = 100
        target_chunks = [list_chembl[i:i + CHUNK_SIZE] for i in range(0, len(list_chembl), CHUNK_SIZE)]

        signatures = [
            sea_target_score_batch.s(
                targets=chunk,
                parent_task_id=task_id,
                total_targets=total,
                smiles_key=smiles_key,
            )
            for chunk in target_chunks
        ]
        callback = postprocess_sea_job.s(
            task_id=task_id,
            list_chembl=list_chembl,
            smiles_test=smiles_test,
        )
        result = chord(signatures)(callback)
        # Save the group result so we can track subtask progress
        result.parent.id = task_id
        result.parent.save()

        celery.backend.store_result(
            task_id=task_id,
            result = {
                "status": "PENDING",
                "completed_cnt": 0,
                "total_targets": total,
                "progress": 0,
                "group_id": result.id, 

            }, state="PENDING"
        )
        end = time.time()
        print(f"Submitted SEA job {task_id} with {total} targets in {end - start:.2f} seconds")
        return {"task_id": task_id}
    except Exception as e:
        print(f"Error submitting SEA job: {e}")
        save_progress(task_id, {
                            "status": "ERROR",
                            "progress": 0, 
                            "total_targets": 0,
                            "error": str(e),
                            "failed": True
                            })
        return {"task_id": task_id, "error": str(e)}


@router.get("/result")
async def get_result(request: Request):
    task_id = request.query_params.get("taskId")
    
    meta = celery.backend.get_task_meta(task_id)
    job_info = meta['result'] if meta else None
    if job_info['status'] == "SUCCESS":
        job_info = get_progress(task_id)
        job_info['sea_score_done'] = True
    
    print(job_info)
    
    job_dir = f"{os.getenv('SEA_JOB_DIR')}/{task_id}"
    sea_result_file = f"{job_dir}/sea_output/sea_result.tsv"
    if os.path.exists(sea_result_file):
        table_result = tsv2json(sea_result_file)
        job_info["result"] = table_result
        input_f = f"{job_dir}/input.smi"
        smiles_test = [
            f"{remove_salt(x.strip().split()[0])} {x.strip().split()[1]}"
            for x in open(input_f).read().splitlines() if x.strip()
        ]
        job_info['input'] = smiles_test
    return job_info


@router.get("/recent_tasks")
def get_recent_tasks():
    recent_tasks = []
    job_dir = os.getenv('SEA_JOB_DIR')
    all_tasks = os.listdir(job_dir)
    for task in all_tasks:
        time_stamp = datetime.fromtimestamp(os.path.getmtime(f"{job_dir}/{task}"))
        formatted_time = time_stamp.strftime("%Y-%m-%d %H:%M:%S")
        task_info = {
            "task_id": task,
            "time_stamp": formatted_time
        }
        recent_tasks.append(task_info)
    # sort by time_stamp descending
    recent_tasks = sorted(recent_tasks, key=lambda x: x['time_stamp'], reverse=True)
    return {"recent_tasks": recent_tasks}

# download result file
@router.get("/download")
def download_result_file(request: Request):
    task_id = request.query_params.get("taskId")
    ## zip the result folder
    job_result_dir = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/sea_output"
    zip_file = f"{job_result_dir}.zip"
    shutil.make_archive(job_result_dir, 'zip', job_result_dir)
    return FileResponse(zip_file, media_type='application/zip', filename=f"sea_result_{task_id}.zip")

@router.get("/compound_vs_target")
def compound_vs_target(request : Request):
    task_id = request.query_params.get("task_id").split("?")[0]
    # return task_id
    heatmap_file = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/.heatmap"
    if not os.path.exists(heatmap_file):
        return {"error", "Heatmap data not found"}
    # read json 
    with open(heatmap_file) as hm_file:
        heatmap_data = json.load(hm_file)
    # print(json.dumps(heatmap_data, indent=4))
    return heatmap_data

@router.get("/best_similarity")
def get_best_similarity_per_target(request: Request):
    task_id = request.query_params.get("taskId").split('?')[0]
    target_chembl_id = request.query_params.get("target_chembl_id")
    print("Target ChEMBL ID:", target_chembl_id)
    print("Fetching best similarity for target:", target_chembl_id)
    best_similarity_file = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/sea_output/top10_{target_chembl_id}.tsv"
    if not os.path.exists(best_similarity_file):
        return {"error": "Best similarity file not found for the given target."}
    best_similarity_json = tsv2json(best_similarity_file)
    # group the data by 'Name' and collect entries
    result_best_similarity = []
    query_molecules_set = set()
    for entry in best_similarity_json:
        name = entry['Name']
        if name not in query_molecules_set:
            query_molecules_set.add(name)
            result_best_similarity.append({
                'Name': name,
                'SMILES': entry['SMILES'],
                'Similar_Known_Hits': []
            })
        for res in result_best_similarity:
            if res['Name'] == name:
                res['Similar_Known_Hits'].append({
                    'Known_Hit_ID': entry['Known_Hit_ID'],
                    'Known_Hit_SMILES': entry['Known_Hit_SMILES'],
                    'Tanimoto_Coefficient': entry['Tanimoto_Score']
                })
                break
    return {"task_id": task_id,
            "query_compounds": list(query_molecules_set),
            "target_chembl_id": target_chembl_id,
            "best_similarity": best_similarity_json,
            "grouped_best_similarity": result_best_similarity}

@router.post("/warm_cache")
def warm_cache():
    """Trigger Redis cache warming for target files."""
    task = warm_target_cache.delay()
    return {"message": "Cache warming started", "task_id": task.id}

@router.get("/cache_info")
def cache_info():
    """Get information about the Redis cache."""
    import redis as redis_module

    # Connect to cache DB
    r = redis_module.Redis(
        host=os.getenv("CELERY_BROKER_URL", "redis://localhost:6380/0").split("://")[1].split(":")[0],
        port=int(os.getenv("CELERY_BROKER_URL", "redis://localhost:6380/0").split(":")[2].split("/")[0]),
        db=2
    )

    # Count cached targets
    cached_keys = r.keys("target:*")
    total_targets = len(_TARGET_FILES_CACHE)

    return {
        "cached_targets": len(cached_keys),
        "total_targets": total_targets,
        "cache_hit_rate": f"{(len(cached_keys) / total_targets * 100):.1f}%" if total_targets > 0 else "N/A"
    }



## TODO: the structure is very messy, need to clean up after
# Put all API routes under /api
app.include_router(router)

# frontend import
frontend_path = os.path.join(os.path.dirname(__file__), "../../frontend/dist")
from starlette.exceptions import HTTPException as StarletteHTTPException
@app.exception_handler(StarletteHTTPException)
async def spa_exception_handler(request: Request, exc: StarletteHTTPException):
    # For 404 errors, serve index.html (let React Router handle it)
    if exc.status_code == 404:
        return FileResponse(os.path.join(frontend_path, "index.html"))
    # For other HTTP errors, raise them normally
    return exc

app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")