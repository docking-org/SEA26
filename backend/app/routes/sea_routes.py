import os
import shutil
import uuid
import json
import pickle
import time
from datetime import datetime
from typing import Union

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from altcha import verify_solution_v1
from celery import chord

from ..celery_worker import (
    celery,
    _mkdir_,
    remove_salt,
    sea_target_score_batch,
    postprocess_sea_job,
    redis_cache,
    acquire_job_slot,
    release_job_slot,
    RateLimitExceeded,
)
from ..helper import (
    tsv2json,
    init_progress_file,
    save_progress,
    get_progress,
    load_gev_params,
    validate_smiles_input,
)
from ..utils.sea_tools import get_target_files

router = APIRouter()


@router.post("/submit")
async def submit_task(request: Request) -> dict[str, str]:
    input_data = await request.json()
    query = input_data["query"]

    non_empty_lines = [l for l in query.strip().splitlines() if l.strip()]
    if len(non_empty_lines) == 0:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")
    if len(non_empty_lines) > 10:
        raise HTTPException(status_code=400, detail=f"Query is limited to 10 compounds. You submitted {len(non_empty_lines)}.")

    invalid_smiles = validate_smiles_input(query)
    if invalid_smiles:
        lines_detail = "; ".join([f"Line {line_num}: '{line}'" for line_num, line in invalid_smiles])
        raise HTTPException(status_code=400, detail=f"Invalid SMILES input. Please check the following lines: {lines_detail}")

    api_key = request.headers.get("X-API-Key")
    api_key_secret = os.getenv("SEA_API_KEY")
    is_api_client = api_key_secret and api_key == api_key_secret

    hmac_secret = os.getenv("ALTCHA_HMAC_SECRET")
    if hmac_secret and not is_api_client:
        altcha_payload = input_data.get("altcha")
        if not altcha_payload:
            raise HTTPException(status_code=400, detail="CAPTCHA verification required")
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


async def _submit_sea_job(task_id: str, input_data: str):
    start = time.time()
    try:
        if not input_data:
            raise HTTPException(status_code=400, detail="Input data is empty.")
        with open(f"{os.getenv('SEA_JOB_DIR')}/{task_id}/input.smi", "w") as f:
            f.write(input_data)
        smiles_test = [
            f"{remove_salt(parts[0])} {parts[1] if len(parts) > 1 else parts[0]}"
            for x in input_data.splitlines()
            if x.strip()
            for parts in [x.strip().split()]
        ]

        data_folder = os.getenv("SEA_DATA_FOLDER")
        tc_cutoff = 0.29
        gev_bin_width = 0.25
        gev_freq_file = os.path.join(data_folder, "0.29_orig_freq.tsv")
        gev_cache_file = os.path.join(data_folder, f"gev_params_tc{tc_cutoff}_bw{gev_bin_width}.tsv")

        if os.path.exists(gev_freq_file):
            gev_c, gev_loc, gev_scale = load_gev_params(gev_freq_file, gev_cache_file, bin_width=gev_bin_width)
        else:
            gev_c, gev_loc, gev_scale = 0.0, 0.0, 1.0

        job_gev_dest = os.path.join(os.getenv("SEA_JOB_DIR"), task_id, "gev_params.tsv")
        if os.path.exists(gev_cache_file):
            shutil.copy2(gev_cache_file, job_gev_dest)

        list_chembl = get_target_files()
        total = len(list_chembl)
        save_progress(task_id, {"status": "PENDING", "progress": 0, "total_targets": total})

        smiles_key = f"input:{task_id}"
        redis_cache.setex(smiles_key, 3600, pickle.dumps(smiles_test))

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
        result.parent.id = task_id
        result.parent.save()

        celery.backend.store_result(
            task_id=task_id,
            result={
                "status": "RUNNING",
                "completed_cnt": 0,
                "total_targets": total,
                "progress": 0,
                "group_id": result.id,
            },
            state="RUNNING",
        )
        save_progress(task_id, {"status": "RUNNING"})
        print(f"Submitted SEA job {task_id} with {total} targets in {time.time() - start:.2f} seconds")
        return {"task_id": task_id}
    except Exception as e:
        print(f"Error submitting SEA job: {e}")
        save_progress(task_id, {
            "status": "FAILED",
            "progress": 0,
            "total_targets": 0,
            "error": str(e),
            "failed": True,
        })
        return {"task_id": task_id, "error": str(e)}


@router.get("/result")
async def get_result(request: Request):
    task_id = request.query_params.get("taskId")

    job_info = get_progress(task_id)

    meta = celery.backend.get_task_meta(task_id)
    celery_result = meta.get("result") if meta else None
    if isinstance(celery_result, dict) and "progress" in celery_result:
        job_info["progress"] = celery_result["progress"]
        job_info["completed_cnt"] = celery_result.get("completed_cnt", 0)

    if job_info.get("status") == "SUCCESS":
        job_info["sea_score_done"] = True

    print(job_info)

    job_dir = f"{os.getenv('SEA_JOB_DIR')}/{task_id}"
    sea_result_file = f"{job_dir}/sea_output/sea_result.tsv"
    if os.path.exists(sea_result_file):
        job_info["result"] = tsv2json(sea_result_file)
        input_f = f"{job_dir}/input.smi"
        smiles_test = [
            f"{remove_salt(parts[0])} {parts[1] if len(parts) > 1 else parts[0]}"
            for x in open(input_f).read().splitlines()
            if x.strip()
            for parts in [x.strip().split()]
        ]
        job_info["input"] = smiles_test
    return job_info


@router.get("/recent_tasks")
def get_recent_tasks():
    job_dir = os.getenv("SEA_JOB_DIR")
    recent_tasks = []
    for task in os.listdir(job_dir):
        time_stamp = datetime.fromtimestamp(os.path.getmtime(f"{job_dir}/{task}"))
        progress = get_progress(task)
        recent_tasks.append({
            "task_id": task,
            "time_stamp": time_stamp.strftime("%Y-%m-%d %H:%M:%S"),
            "status": progress.get("status", "UNKNOWN") if progress else "UNKNOWN",
        })
    recent_tasks.sort(key=lambda x: x["time_stamp"], reverse=True)
    return {"recent_tasks": recent_tasks}


@router.get("/download")
def download_result_file(request: Request):
    task_id = request.query_params.get("taskId")
    job_result_dir = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/sea_output"
    zip_file = f"{job_result_dir}.zip"
    shutil.make_archive(job_result_dir, "zip", job_result_dir)
    return FileResponse(zip_file, media_type="application/zip", filename=f"sea_result_{task_id}.zip")


@router.get("/compound_vs_target")
def compound_vs_target(request: Request):
    task_id = request.query_params.get("task_id").split("?")[0]
    heatmap_file = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/.heatmap"
    if not os.path.exists(heatmap_file):
        return {"error": "Heatmap data not found"}
    with open(heatmap_file) as f:
        return json.load(f)


@router.get("/best_similarity")
def get_best_similarity_per_target(request: Request):
    task_id = request.query_params.get("taskId").split("?")[0]
    target_chembl_id = request.query_params.get("target_chembl_id")
    print("Fetching best similarity for target:", target_chembl_id)
    best_similarity_file = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/sea_output/top10_{target_chembl_id}.tsv"
    if not os.path.exists(best_similarity_file):
        return {"error": "Best similarity file not found for the given target."}
    best_similarity_json = tsv2json(best_similarity_file)
    result_best_similarity = []
    query_molecules_set = set()
    for entry in best_similarity_json:
        name = entry["Name"]
        if name not in query_molecules_set:
            query_molecules_set.add(name)
            result_best_similarity.append({
                "Name": name,
                "SMILES": entry["SMILES"],
                "Similar_Known_Hits": [],
            })
        for res in result_best_similarity:
            if res["Name"] == name:
                res["Similar_Known_Hits"].append({
                    "Known_Hit_ID": entry["Known_Hit_ID"],
                    "Known_Hit_SMILES": entry["Known_Hit_SMILES"],
                    "Tanimoto_Coefficient": entry["Tanimoto_Score"],
                })
                break
    return {
        "task_id": task_id,
        "query_compounds": list(query_molecules_set),
        "target_chembl_id": target_chembl_id,
        "best_similarity": best_similarity_json,
        "grouped_best_similarity": result_best_similarity,
    }
