import os
import sys
import glob
import shutil
import uuid
import json
import asyncio
import subprocess
import pandas as pd
from typing import Union
from datetime import datetime
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, APIRouter, HTTPException
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

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
from .celery_worker import _mkdir_, remove_salt, sea_target_score, sea_target_score_batch, warm_target_cache, postprocess_sea_job, redis_cache, acquire_job_slot, release_job_slot, RateLimitExceeded, r as _redis
from .helper import get_smiles_property, known_test_tc, tsv2json, init_progress_file, save_progress, get_progress, load_gev_params, validate_smiles_input
from .scripts.generate_boltz_jobs import generate_jobs as _generate_boltz_jobs

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

@router.get("/captcha/challenge", include_in_schema=False)
def get_captcha_challenge():
    """Generate a new ALTCHA proof-of-work challenge."""
    hmac_secret = os.getenv("ALTCHA_HMAC_SECRET")
    if not hmac_secret:
        return {"disabled": True}
    challenge = create_challenge_v1(hmac_key=hmac_secret, max_number=100000)
    return challenge.to_dict()

@router.post("/submit")
async def submit_task(request: Request) -> dict[str, str]:
    input_data = await request.json()
    query = input_data["query"]

    # Limit query to 10 lines
    non_empty_lines = [l for l in query.strip().splitlines() if l.strip()]
    if len(non_empty_lines) == 0:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")
    if len(non_empty_lines) > 10:
        raise HTTPException(status_code=400, detail=f"Query is limited to 10 compounds. You submitted {len(non_empty_lines)}.")

    invalid_smiles = validate_smiles_input(query)
    if invalid_smiles:
        lines_detail = "; ".join([f"Line {line_num}: '{line}'" for line_num, line in invalid_smiles])
        raise HTTPException(status_code=400, detail=f"Invalid SMILES input. Please check the following lines: {lines_detail}")

    # Verify ALTCHA captcha (skipped when ALTCHA_HMAC_SECRET is unset)
    hmac_secret = os.getenv("ALTCHA_HMAC_SECRET")
    if hmac_secret:
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


# sea background task submission using Celery
async def _submit_sea_job(task_id: str, input_data: dict):
    # setup job directory
    import time
    start = time.time()
    try:
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
    # Sort by time_stamp descending
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
    return heatmap_data

@router.get("/best_similarity")
def get_best_similarity_per_target(request: Request):
    task_id = request.query_params.get("taskId").split('?')[0]
    target_chembl_id = request.query_params.get("target_chembl_id")
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

@router.get("/boltz_status")
def boltz_status(request: Request):
    task_id = request.query_params.get("task_id")
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id required")
    manifest_path = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/boltz_jobs/boltz_manifest.tsv"
    if not os.path.exists(manifest_path):
        return {"jobs_by_chembl": {}}
    manifest = pd.read_csv(manifest_path, sep="\t")
    jobs_by_chembl = (
        manifest.groupby("CHEMBL ID")["job"]
        .apply(list)
        .to_dict()
    )
    return {"jobs_by_chembl": jobs_by_chembl}

@router.get("/boltz_run_sh")
def boltz_run_sh(request: Request):
    task_id  = request.query_params.get("task_id")
    chembl_id = request.query_params.get("chembl_id")
    if not task_id or not chembl_id:
        raise HTTPException(status_code=400, detail="task_id and chembl_id required")
    manifest_path = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/boltz_jobs/boltz_manifest.tsv"
    if not os.path.exists(manifest_path):
        raise HTTPException(status_code=404, detail="No Boltz jobs found for this task")
    manifest = pd.read_csv(manifest_path, sep="\t")
    rows = manifest[manifest["CHEMBL ID"] == chembl_id]
    if rows.empty:
        raise HTTPException(status_code=404, detail=f"No Boltz jobs for {chembl_id}")
    lines = ["#!/usr/bin/env bash", "set -euo pipefail",
             f"## Boltz-2 jobs for {chembl_id} — {len(rows)} ligand(s)", ""]
    for _, row in rows.iterrows():
        lines.append(f"# {row['job']}")
        lines.append(f"bash {row['run']}")
        lines.append("")
    script = "\n".join(lines)
    from fastapi.responses import Response
    return Response(
        content=script,
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename=\"run_boltz_{chembl_id}.sh\""}
    )

@router.post("/generate_boltz_jobs")
async def generate_boltz_jobs_endpoint(request: Request):
    body = await request.json()
    task_id = body.get("task_id")
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id required")

    job_root   = os.getenv("SEA_JOB_DIR")
    result_dir = f"{job_root}/{task_id}/sea_output"
    boltz_out  = f"{job_root}/{task_id}/boltz_jobs"
    test_file  = f"{job_root}/{task_id}/input.smi"
    lookup_file = os.path.join(os.getenv("SEA_DATA_FOLDER"), "uniprot_chembl_id.tsv")
    log_file   = f"{job_root}/{task_id}/logs/boltz.log"

    try:
        manifest = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _generate_boltz_jobs(
                result_dir=result_dir,
                testFile=test_file,
                out_dir=boltz_out,
                tc_min=0.0,
                lookup_file=lookup_file,
                predict_affinity=True,
                log_file=log_file,
                boltz_bin=os.getenv("BOLTZ_BIN", "/nfs/home/khtang/.conda/envs/sea25/bin/boltz"),
            )
        )
        return {"status": "success", "n_jobs": int(manifest.shape[0]), "out_dir": boltz_out}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def _slurm_status(job_id: str) -> str:
    r = subprocess.run(["squeue", f"--jobs={job_id}", "-h", "-o", "%T"],
                       capture_output=True, text=True)
    return r.stdout.strip() or "UNKNOWN"

_SLURM_STATE_MAP = {
    "PENDING": "pending", "CONFIGURING": "pending",
    "RUNNING": "running", "COMPLETING": "running",
    "COMPLETED": "done",
    "FAILED": "failed", "CANCELLED": "failed", "TIMEOUT": "failed",
    "OUT_OF_MEMORY": "failed",
}

_TERMINAL_STATES = {"done", "failed"}

@router.post("/boltz_submit_slurm")
async def boltz_submit_slurm(request: Request):
    body = await request.json()
    task_id   = body.get("task_id")
    chembl_id = body.get("chembl_id")
    ligand    = body.get("ligand")  # optional; if omitted, runs all ligands for this target
    if not task_id or not chembl_id:
        raise HTTPException(status_code=400, detail="task_id and chembl_id required")

    # Verify ALTCHA captcha (skipped when ALTCHA_HMAC_SECRET is unset)
    hmac_secret = os.getenv("ALTCHA_HMAC_SECRET")
    if hmac_secret:
        altcha_payload = body.get("altcha")
        if not altcha_payload:
            raise HTTPException(status_code=400, detail="CAPTCHA verification required")
        verified, err = verify_solution_v1(altcha_payload, hmac_secret)
        if not verified:
            raise HTTPException(status_code=400, detail="CAPTCHA verification failed")

    boltz_dir = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/boltz_jobs"
    manifest_path = f"{boltz_dir}/boltz_manifest.tsv"
    if not os.path.exists(manifest_path):
        raise HTTPException(status_code=404, detail="No Boltz jobs found for this task")

    manifest = pd.read_csv(manifest_path, sep="\t")
    rows = manifest[manifest["CHEMBL ID"] == chembl_id]
    if ligand:
        rows = rows[rows["Ligand"] == ligand]
    if rows.empty:
        raise HTTPException(status_code=404, detail=f"No Boltz jobs for {chembl_id}" + (f" / {ligand}" if ligand else ""))

    job_key = f"{chembl_id}__{ligand}" if ligand else chembl_id
    script_tag = f"{chembl_id}__{ligand}" if ligand else chembl_id

    # Build sbatch wrapper that calls each existing run.sh sequentially
    nodelist  = os.getenv("SLURM_NODELIST")
    partition = os.getenv("SLURM_PARTITION")
    account   = os.getenv("SLURM_ACCOUNT")
    qos       = os.getenv("SLURM_QOS")
    time_     = os.getenv("SLURM_TIME")
    gpus      = os.getenv("SLURM_GPUS")

    lines = [
        "#!/usr/bin/env bash",
        f"#SBATCH --job-name=sea_boltz2_{script_tag}",
        f"#SBATCH --partition={partition}",
        f"#SBATCH --nodelist={nodelist}",
    ]
    lines += [
        f"#SBATCH --time={time_}",
        f"#SBATCH --gres=gpu:{gpus}",
        f"#SBATCH --output={boltz_dir}/slurm_{script_tag}_%j.log",
        "",
    ]
    for _, row in rows.iterrows():
        lines.append(f"bash {row['run']}")
    lines.append("")

    slurm_script = f"{boltz_dir}/slurm_{script_tag}.sh"
    with open(slurm_script, "w") as f:
        f.write("\n".join(lines))
    os.chmod(slurm_script, 0o755)

    result = subprocess.run(["sbatch", slurm_script], capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"sbatch failed: {result.stderr.strip()}")

    job_id = result.stdout.strip().split()[-1]

    jobs_file = f"{boltz_dir}/.slurm_jobs.json"
    slurm_jobs = {}
    if os.path.exists(jobs_file):
        with open(jobs_file) as f:
            slurm_jobs = json.load(f)
    slurm_jobs[job_key] = {
        "slurm_job_id": job_id,
        "chembl_id": chembl_id,
        "ligand": ligand,
        "submitted_at": datetime.utcnow().isoformat(),
    }
    with open(jobs_file, "w") as f:
        json.dump(slurm_jobs, f)

    return {"slurm_job_id": job_id}

@router.post("/boltz_submit_from_heatmap", include_in_schema=False)
async def boltz_submit_from_heatmap(request: Request):
    from .scripts.generate_boltz_jobs import write_yaml, sanitize
    body = await request.json()
    task_id     = body.get("task_id")
    chembl_id   = body.get("chembl_id")
    smiles      = body.get("smiles")
    ligand_name = body.get("ligand_name", "query")
    if not task_id or not chembl_id or not smiles:
        raise HTTPException(status_code=400, detail="task_id, chembl_id, and smiles required")

    # Verify ALTCHA captcha (skipped when ALTCHA_HMAC_SECRET is unset)
    hmac_secret = os.getenv("ALTCHA_HMAC_SECRET")
    if hmac_secret:
        altcha_payload = body.get("altcha")
        if not altcha_payload:
            raise HTTPException(status_code=400, detail="CAPTCHA verification required")
        verified, err = verify_solution_v1(altcha_payload, hmac_secret)
        if not verified:
            raise HTTPException(status_code=400, detail="CAPTCHA verification failed")

    # Enforce queue limit: max n queued jobs waiting for SLURM
    BOLTZ_LIMITS = os.getenv("BOLTZ_LIMITS", "4/20")
    MAX_PENDING = int(BOLTZ_LIMITS.split("/")[-1])
    queue_len = _redis.llen("boltz_queue")
    if queue_len >= MAX_PENDING:
        raise HTTPException(status_code=429, detail=f"Queue full ({queue_len}/{MAX_PENDING} jobs waiting). Please try again later.")

    # Read protein sequence from sea_result.tsv
    sea_result_path = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/sea_output/sea_result.tsv"
    if not os.path.exists(sea_result_path):
        raise HTTPException(status_code=404, detail="SEA results not found for this task")
    targets = pd.read_csv(sea_result_path, sep="\t")
    rows = targets[targets["Target_Chembl_ID"] == chembl_id]
    if rows.empty:
        raise HTTPException(status_code=404, detail=f"Target {chembl_id} not found in SEA results")

    chains = [
        {"sequence": str(r["Sequence"]).strip(),
         "label": str(r.get("Uniprot_ID", chembl_id))}
        for _, r in rows.iterrows()
        if str(r.get("Sequence", "")).strip()
    ]
    if not chains:
        raise HTTPException(status_code=400, detail=f"No protein sequence available for {chembl_id}")

    lig_safe = sanitize(ligand_name)
    job_name  = f"{sanitize(chembl_id)}__{lig_safe}"
    boltz_dir = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/boltz_jobs"
    job_dir   = f"{boltz_dir}/{job_name}"
    os.makedirs(job_dir, exist_ok=True)

    # Write Boltz2 input YAML
    yaml_path = f"{job_dir}/{job_name}.yaml"
    if os.path.exists(yaml_path):
        os.remove(yaml_path)
    write_yaml(yaml_path, chains, smiles, msa_mode="server", msa_dir="", predict_affinity=True)

    # Write per-job run.sh
    boltz_bin = os.getenv("BOLTZ_BIN")
    import shlex
    cmd = f"{boltz_bin} predict {shlex.quote(os.path.abspath(yaml_path))} --out_dir {shlex.quote(os.path.abspath(os.path.join(job_dir, 'boltz_out')))} --use_msa_server"
    run_path = f"{job_dir}/run.sh"
    if os.path.exists(run_path):
        os.remove(run_path)
    with open(run_path, "w") as f:
        f.write("#!/usr/bin/env bash\nset -euo pipefail\n"
                f"export HOME={os.path.abspath(job_dir)}\n"
                f"export NUMBA_CACHE_DIR={os.path.abspath(job_dir)}/numba_cache\n"
                f"export MPLCONFIGDIR={os.path.abspath(job_dir)}/mpl_cache\n"
                "export LD_LIBRARY_PATH=/nfs/home/khtang/.conda/envs/sea25/lib\n"
                f"{cmd}\n")
    os.chmod(run_path, 0o755)

    # Build and submit SLURM wrapper
    nodelist  = os.getenv("SLURM_NODELIST")
    partition = os.getenv("SLURM_PARTITION")
    time_     = os.getenv("SLURM_TIME")
    gpus      = os.getenv("SLURM_GPUS")

    sbatch_lines = [
        "#!/usr/bin/env bash",
        f"#SBATCH --job-name=sea_boltz2_{job_name}",
        f"#SBATCH --partition={partition}",
        f"#SBATCH --nodelist={nodelist}",
    ]
    sbatch_lines += [
        f"#SBATCH --time={time_}",
        f"#SBATCH --gres=gpu:{gpus}",
        f"#SBATCH --output={boltz_dir}/slurm_{job_name}_%j.log",
        "",
        f"bash {os.path.abspath(run_path)}",
        "",
    ]
    slurm_script = f"{boltz_dir}/slurm_{job_name}.sh"
    with open(slurm_script, "w") as f:
        f.write("\n".join(sbatch_lines))
    os.chmod(slurm_script, 0o755)

    # Enqueue into Redis for the drain task to submit to SLURM when a slot opens
    _redis.rpush("boltz_queue", json.dumps({
        "task_id":      task_id,
        "job_key":      job_name,
        "boltz_dir":    boltz_dir,
        "slurm_script": slurm_script,
    }))

    jobs_file = f"{boltz_dir}/.slurm_jobs.json"
    slurm_jobs = {}
    if os.path.exists(jobs_file):
        with open(jobs_file) as f:
            slurm_jobs = json.load(f)
    slurm_jobs[job_name] = {
        "slurm_job_id": None,
        "chembl_id": chembl_id,
        "ligand": ligand_name,
        "status": "queued",
        "queued_at": datetime.utcnow().isoformat(),
    }
    with open(jobs_file, "w") as f:
        json.dump(slurm_jobs, f)

    return {"status": "queued", "job_key": job_name}

@router.get("/boltz_slurm_status")
def boltz_slurm_status(request: Request):
    task_id = request.query_params.get("task_id")
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id required")

    boltz_dir = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/boltz_jobs"
    jobs_file = f"{boltz_dir}/.slurm_jobs.json"
    if not os.path.exists(jobs_file):
        return {}

    with open(jobs_file) as f:
        slurm_jobs = json.load(f)

    dirty = False
    out = {}
    for job_key, info in slurm_jobs.items():
        job_id = info.get("slurm_job_id")
        cached = info.get("status")
        # Jobs still in the Redis queue have no SLURM id yet
        if not job_id or cached == "queued":
            status = "queued"
        elif cached in _TERMINAL_STATES:
            status = cached
        else:
            raw    = _slurm_status(str(job_id))
            status = _SLURM_STATE_MAP.get(raw, "unknown")
            # sacct may be unreachable; fall back to checking output files
            if status == "unknown":
                pred_base = os.path.join(boltz_dir, job_key, "boltz_out",
                                         f"boltz_results_{job_key}", "predictions", job_key)
                if os.path.isdir(pred_base) and glob.glob(os.path.join(pred_base, "*.cif")):
                    status = "done"
            if status in _TERMINAL_STATES:
                slurm_jobs[job_key]["status"] = status
                dirty = True
        out[job_key] = {
            "slurm_job_id": job_id,
            "chembl_id":    info.get("chembl_id", job_key),
            "ligand":       info.get("ligand"),
            "status":       status,
        }

    if dirty:
        with open(jobs_file, "w") as f:
            json.dump(slurm_jobs, f)

    return out

@router.get("/boltz_download")
def boltz_download(request: Request):
    task_id   = request.query_params.get("task_id")
    chembl_id = request.query_params.get("chembl_id")
    ligand    = request.query_params.get("ligand")  # optional compound filter
    if not task_id or not chembl_id:
        raise HTTPException(status_code=400, detail="task_id and chembl_id required")

    boltz_dir = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/boltz_jobs"

    # Collect matching job names from manifest (pre-generated jobs) and
    # from .slurm_jobs.json (jobs submitted directly from the heatmap).
    job_names = []

    manifest_path = f"{boltz_dir}/boltz_manifest.tsv"
    if os.path.exists(manifest_path):
        manifest = pd.read_csv(manifest_path, sep="\t")
        rows = manifest[manifest["CHEMBL ID"] == chembl_id]
        if ligand:
            rows = rows[rows["Ligand"] == ligand]
        job_names.extend(rows["job"].tolist())

    jobs_file = f"{boltz_dir}/.slurm_jobs.json"
    if os.path.exists(jobs_file):
        with open(jobs_file) as _jf:
            slurm_jobs = json.load(_jf)
        for jkey, info in slurm_jobs.items():
            if info.get("chembl_id") == chembl_id:
                if ligand is None or info.get("ligand") == ligand:
                    if jkey not in job_names:
                        job_names.append(jkey)

    if not job_names:
        raise HTTPException(status_code=404, detail=f"No Boltz results for {chembl_id}" + (f" / {ligand}" if ligand else ""))

    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for job_name in job_names:
            pred_dir = os.path.join(boltz_dir, job_name, "boltz_out",
                                    f"boltz_results_{job_name}", "predictions", job_name)
            if not os.path.isdir(pred_dir):
                continue
            for fpath in glob.glob(os.path.join(pred_dir, "**", "*"), recursive=True):
                if os.path.isfile(fpath):
                    arcname = os.path.join(job_name, os.path.relpath(fpath, pred_dir))
                    zf.write(fpath, arcname=arcname)
    buf.seek(0)
    filename = f"boltz_{task_id}_{chembl_id}" + (f"_{ligand}" if ligand else "") + ".zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=\"{filename}\""}
    )

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
    if exc.status_code == 404:
        # Return proper JSON 404 for API routes
        if request.url.path.startswith("/api"):
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        # For non-API routes, serve index.html (let React Router handle it)
        return FileResponse(os.path.join(frontend_path, "index.html"), status_code=404)
    # For other HTTP errors, return a proper JSON response
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")