import os
import sys
import shutil
import uuid
import json
import asyncio
import pandas as pd
from typing import Union
from datetime import datetime
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, APIRouter, HTTPException
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from celery.utils.log import get_task_logger
from dotenv import load_dotenv, find_dotenv
from altcha import create_challenge_v1, verify_solution_v1

# import sea analysis functions
import numpy as np
import uuid, asyncio
from .celery_worker import celery
from celery import group, chord
from celery.result import AsyncResult, GroupResult
import pickle
from .celery_worker import _mkdir_, remove_salt, sea_target_score, sea_target_score_batch, warm_target_cache, postprocess_sea_job, redis_cache, acquire_job_slot, release_job_slot, RateLimitExceeded
from .helper import get_smiles_property, known_test_tc, tsv2json, init_progress_file, save_progress, get_progress, load_gev_params, validate_smiles_input

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

@router.get("/captcha/challenge")
def get_captcha_challenge():
    """Generate a new ALTCHA proof-of-work challenge."""
    hmac_secret = os.getenv("ALTCHA_HMAC_SECRET")
    if not hmac_secret:
        raise HTTPException(status_code=500, detail="ALTCHA not configured")
    challenge = create_challenge_v1(hmac_key=hmac_secret, max_number=100000)
    return challenge.to_dict()

@router.post("/submit")
async def submit_task(request: Request) -> dict[str, str]:
    input_data = await request.json()
    query = input_data["query"]

    # Limit query to 10 lines
    non_empty_lines = [l for l in query.strip().splitlines() if l.strip()]
    if len(non_empty_lines) > 10:
        raise HTTPException(status_code=400, detail=f"Query is limited to 10 compounds. You submitted {len(non_empty_lines)}.")

    invalid_smiles = validate_smiles_input(query)
    if invalid_smiles:
        lines_detail = "; ".join([f"Line {line_num}: '{line}'" for line_num, line in invalid_smiles])
        raise HTTPException(status_code=400, detail=f"Invalid SMILES input. Please check the following lines: {lines_detail}")

    # Verify ALTCHA captcha
    altcha_payload = input_data.get("altcha")
    if not altcha_payload:
        raise HTTPException(status_code=400, detail="CAPTCHA verification required")
    hmac_secret = os.getenv("ALTCHA_HMAC_SECRET")
    verified, err = verify_solution_v1(altcha_payload, hmac_secret)
    if not verified:
        raise HTTPException(status_code=400, detail="CAPTCHA verification failed")

    task_id = str(uuid.uuid4())
    job_dir = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/sea_output"
    _mkdir_(job_dir)
    init_progress_file(task_id)

    try:
        acquire_job_slot(task_id)
    except RateLimitExceeded:
        return JSONResponse(status_code=429, content={"detail": "Server is busy. Please try again later."})

    result = await _submit_sea_job(task_id, query)
    if "error" in result:
        release_job_slot(task_id)
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
        # Load GEV params (shared cache in SEA_DATA_FOLDER; fits on first-ever run)
        data_folder = os.getenv("SEA_DATA_FOLDER")
        tc_cutoff = 0.29
        gev_bin_width = 0.25
        gev_freq_file = os.path.join(data_folder, "0.29_orig_freq.tsv")
        gev_cache_file = os.path.join(data_folder, f"gev_params_tc{tc_cutoff}_bw{gev_bin_width}.tsv")

        if os.path.exists(gev_freq_file):
            gev_c, gev_loc, gev_scale = load_gev_params(gev_freq_file, gev_cache_file, bin_width=gev_bin_width)
        else:
            gev_c, gev_loc, gev_scale = 0.0, 0.0, 1.0

        # Per-job copy for reproducibility / audit
        job_gev_dest = os.path.join(os.getenv("SEA_JOB_DIR"), task_id, "gev_params.tsv")
        if os.path.exists(gev_cache_file):
            shutil.copy2(gev_cache_file, job_gev_dest)

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
                tc_cutoff=tc_cutoff,
                gev_c=gev_c,
                gev_loc=gev_loc,
                gev_scale=gev_scale,
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
                "status": "RUNNING",
                "completed_cnt": 0,
                "total_targets": total,
                "progress": 0,
                "group_id": result.id,

            }, state="RUNNING"
        )
        save_progress(task_id, {"status": "RUNNING"})
        end = time.time()
        print(f"Submitted SEA job {task_id} with {total} targets in {end - start:.2f} seconds")
        return {"task_id": task_id}
    except Exception as e:
        print(f"Error submitting SEA job: {e}")
        save_progress(task_id, {
                            "status": "FAILED",
                            "progress": 0, 
                            "total_targets": 0,
                            "error": str(e),
                            "failed": True
                            })
        return {"task_id": task_id, "error": str(e)}


@router.get("/result")
async def get_result(request: Request):
    task_id = request.query_params.get("taskId")
    
    # Use .progress file as source of truth for status
    job_info = get_progress(task_id)

    # Merge real-time progress from Celery backend during batch scoring
    meta = celery.backend.get_task_meta(task_id)
    celery_result = meta.get('result') if meta else None
    if isinstance(celery_result, dict) and 'progress' in celery_result:
        job_info['progress'] = celery_result['progress']
        job_info['completed_cnt'] = celery_result.get('completed_cnt', 0)

    if job_info.get('status') == "SUCCESS":
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
        progress = get_progress(task)
        task_info = {
            "task_id": task,
            "time_stamp": formatted_time,
            "status": progress.get("status", "UNKNOWN") if progress else "UNKNOWN",
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
    # For other HTTP errors, return a proper JSON response
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")