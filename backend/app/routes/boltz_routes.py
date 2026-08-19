import os
import glob
import hashlib
import io
import json
import fcntl
import shutil
import subprocess
import uuid
import zipfile
import asyncio
from contextlib import contextmanager
from datetime import datetime

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from altcha import verify_solution_v1

from ..celery_worker import run_boltz_job
from ..scripts.generate_boltz_jobs import generate_jobs as _generate_boltz_jobs, write_yaml, sanitize
from ..utils.auth_tools import _require_internal

router = APIRouter()

_TERMINAL_STATES = {"done", "failed"}


def _verify_captcha(body: dict, request: Request = None):
    hmac_secret = os.getenv("ALTCHA_HMAC_SECRET")
    if not hmac_secret:
        return
    if request is not None:
        api_key_secret = os.getenv("SEA_API_KEY")
        if api_key_secret and request.headers.get("X-API-Key") == api_key_secret:
            return
    altcha_payload = body.get("altcha")
    if not altcha_payload:
        raise HTTPException(status_code=400, detail="CAPTCHA verification required")
    verified, _ = verify_solution_v1(altcha_payload, hmac_secret)
    if not verified:
        raise HTTPException(status_code=400, detail="CAPTCHA verification failed")


def _load_boltz_jobs(jobs_file: str) -> dict:
    if os.path.exists(jobs_file):
        with open(jobs_file) as f:
            return json.load(f)
    return {}


def _save_boltz_jobs(jobs_file: str, jobs: dict):
    import tempfile
    dir_name = os.path.dirname(jobs_file)
    with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, suffix=".tmp") as tf:
        json.dump(jobs, tf)
        tmp_path = tf.name
    os.replace(tmp_path, jobs_file)


@contextmanager
def _boltz_jobs_lock(jobs_file: str):
    """Exclusive POSIX record lock so concurrent web requests and the celery worker
    never interleave their read-modify-write on .boltz_jobs.json."""
    lock_path = jobs_file + ".lock"
    with open(lock_path, "a") as lf:
        fcntl.lockf(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.lockf(lf, fcntl.LOCK_UN)


def _msa_cache_dir() -> str:
    default = os.path.join(os.getenv("SEA_JOB_DIR", "/tmp"), "msa_cache")
    return os.getenv("BOLTZ_MSA_CACHE_DIR", default)


def _msa_cache_path(sequence: str) -> str:
    h = hashlib.sha256(sequence.encode()).hexdigest()[:16]
    return os.path.join(_msa_cache_dir(), f"{h}.a3m")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/boltz_status", dependencies=[Depends(_require_internal)])
def boltz_status(request: Request):
    task_id = request.query_params.get("task_id")
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id required")
    manifest_path = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/boltz_jobs/boltz_manifest.tsv"
    if not os.path.exists(manifest_path):
        return {"jobs_by_chembl": {}}
    manifest = pd.read_csv(manifest_path, sep="\t")
    jobs_by_chembl = manifest.groupby("CHEMBL ID")["job"].apply(list).to_dict()
    return {"jobs_by_chembl": jobs_by_chembl}


@router.get("/boltz_run_sh", dependencies=[Depends(_require_internal)])
def boltz_run_sh(request: Request):
    task_id = request.query_params.get("task_id")
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
    lines = [
        "#!/usr/bin/env bash", "set -euo pipefail",
        f"## Boltz-2 jobs for {chembl_id} — {len(rows)} ligand(s)", "",
    ]
    for _, row in rows.iterrows():
        lines += [f"# {row['job']}", f"bash {row['run']}", ""]
    return Response(
        content="\n".join(lines),
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename=\"run_boltz_{chembl_id}.sh\""},
    )


@router.post("/generate_boltz_jobs", dependencies=[Depends(_require_internal)])
async def generate_boltz_jobs_endpoint(request: Request):
    body = await request.json()
    task_id = body.get("task_id")
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id required")

    job_root = os.getenv("SEA_JOB_DIR")
    result_dir = f"{job_root}/{task_id}/sea_output"
    boltz_out = f"{job_root}/{task_id}/boltz_jobs"
    test_file = f"{job_root}/{task_id}/input.smi"
    lookup_file = os.path.join(os.getenv("SEA_DATA_FOLDER"), "uniprot_chembl_id.tsv")
    log_file = f"{job_root}/{task_id}/logs/boltz.log"

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
            ),
        )
        return {"status": "success", "n_jobs": int(manifest.shape[0]), "out_dir": boltz_out}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/boltz_submit", dependencies=[Depends(_require_internal)])
async def boltz_submit(request: Request):
    body = await request.json()
    task_id = body.get("task_id")
    chembl_id = body.get("chembl_id")
    ligand = body.get("ligand")
    if not task_id or not chembl_id:
        raise HTTPException(status_code=400, detail="task_id and chembl_id required")

    _verify_captcha(body, request)

    boltz_dir = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/boltz_jobs"
    manifest_path = f"{boltz_dir}/boltz_manifest.tsv"
    if not os.path.exists(manifest_path):
        raise HTTPException(status_code=404, detail="No Boltz jobs found for this task")

    manifest = pd.read_csv(manifest_path, sep="\t")
    rows = manifest[manifest["CHEMBL ID"] == chembl_id]
    if ligand:
        rows = rows[rows["Ligand"] == ligand]
    if rows.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No Boltz jobs for {chembl_id}" + (f" / {ligand}" if ligand else ""),
        )

    results = []
    job_name = f"{chembl_id}__{ligand}" if ligand else chembl_id

    for _, row in rows.iterrows():
        run_script = row["run"]
        result = subprocess.run(["bash", run_script], capture_output=True, text=True)
        if result.returncode != 0:
            results.append({"job": row["job"], "status": "failed", "error": result.stderr.strip()})
        else:
            results.append({"job": row["job"], "status": "success", "output": result.stdout.strip()})
    return {"results": results}


@router.post("/boltz_submit_from_heatmap", include_in_schema=False, dependencies=[Depends(_require_internal)])
async def boltz_submit_from_heatmap(request: Request):
    body = await request.json()
    task_id = body.get("task_id")
    chembl_id = body.get("chembl_id")
    smiles = body.get("smiles")
    ligand_name = body.get("ligand_name", "query")
    if not task_id or not chembl_id or not smiles:
        raise HTTPException(status_code=400, detail="task_id, chembl_id, and smiles required")

    _verify_captcha(body, request)

    lig_safe = sanitize(ligand_name)
    job_name = f"{sanitize(chembl_id)}__{lig_safe}"
    boltz_dir = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/boltz_jobs"
    jobs_file = f"{boltz_dir}/.boltz_jobs.json"

    # De-duplication: skip if job already done or in-flight
    existing = _load_boltz_jobs(jobs_file)
    if job_name in existing:
        st = existing[job_name].get("status", "")
        if st == "done":
            return {"status": "already_done", "job_key": job_name}
        if st in ("queued", "running"):
            return {"status": "already_running", "job_key": job_name}
    pred_dir_chk = os.path.join(
        boltz_dir, job_name, "boltz_out",
        f"boltz_results_{job_name}", "predictions", job_name,
    )
    if os.path.isdir(pred_dir_chk) and glob.glob(os.path.join(pred_dir_chk, "*.cif")):
        return {"status": "already_done", "job_key": job_name}

    # Enforce global queue limit (count non-terminal jobs across all tasks)
    max_queued = int(os.getenv("BOLTZ_MAX_QUEUED", "10"))
    job_dir_base = os.getenv("SEA_JOB_DIR", "")
    active_count = 0
    for jf in glob.glob(f"{job_dir_base}/*/boltz_jobs/.boltz_jobs.json"):
        try:
            with open(jf) as _f:
                _jobs = json.load(_f)
            active_count += sum(1 for j in _jobs.values() if j.get("status") not in _TERMINAL_STATES)
        except Exception:
            pass
    if active_count >= max_queued:
        raise HTTPException(status_code=429, detail=f"Queue full ({active_count}/{max_queued} jobs active). Please try again later.")

    # Read protein sequences from sea_result.tsv
    sea_result_path = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/sea_output/sea_result.tsv"
    if not os.path.exists(sea_result_path):
        raise HTTPException(status_code=404, detail="SEA results not found for this task")
    targets = pd.read_csv(sea_result_path, sep="\t")
    rows = targets[targets["Target_Chembl_ID"] == chembl_id]
    if rows.empty:
        raise HTTPException(status_code=404, detail=f"Target {chembl_id} not found in SEA results")

    chains = [
        {"sequence": str(r["Sequence"]).strip(), "label": str(r.get("Uniprot_ID", chembl_id))}
        for _, r in rows.iterrows()
        if str(r.get("Sequence", "")).strip()
    ]
    if not chains:
        raise HTTPException(status_code=400, detail=f"No protein sequence available for {chembl_id}")

    job_dir = f"{boltz_dir}/{job_name}"
    os.makedirs(job_dir, exist_ok=True)

    # --- Fix 3: MSA cache check — skip server call if all chains are cached ---
    msa_cache_targets = []  # chains that need caching after this job completes
    all_cached = all(os.path.exists(_msa_cache_path(c["sequence"])) for c in chains)
    if all_cached:
        # Symlink cached a3ms into a per-job dir named by chain label for write_yaml
        precomp_dir = os.path.join(job_dir, "msa_precomp")
        os.makedirs(precomp_dir, exist_ok=True)
        for chain in chains:
            link = os.path.join(precomp_dir, f"{sanitize(chain['label'])}.a3m")
            if not os.path.exists(link):
                os.symlink(_msa_cache_path(chain["sequence"]), link)
        yaml_path = f"{job_dir}/{job_name}.yaml"
        if os.path.exists(yaml_path):
            os.remove(yaml_path)
        write_yaml(yaml_path, chains, smiles, msa_mode="precomputed", msa_dir=precomp_dir, predict_affinity=True)
        use_msa_server_flag = ""
    else:
        yaml_path = f"{job_dir}/{job_name}.yaml"
        if os.path.exists(yaml_path):
            os.remove(yaml_path)
        write_yaml(yaml_path, chains, smiles, msa_mode="server", msa_dir="", predict_affinity=True)
        use_msa_server_flag = " --use_msa_server"
        # Record which chains to cache after success
        for chain in chains:
            if not os.path.exists(_msa_cache_path(chain["sequence"])):
                msa_cache_targets.append({
                    "seq_hash": hashlib.sha256(chain["sequence"].encode()).hexdigest()[:16],
                    "label": chain["label"],
                })

    # Fix 2: --cache /scratch so boltz loads model weights from local SSD
    boltz_bin = os.getenv("BOLTZ_BIN")
    out_dir = os.path.abspath(os.path.join(job_dir, "boltz_out"))
    boltz_cache = os.getenv("BOLTZ_CACHE_DIR", "/scratch/sea_boltz")
    cmd = (
        f"{boltz_bin} predict {os.path.abspath(yaml_path)}"
        f" --out_dir {out_dir}"
        f"{use_msa_server_flag}"
        f" --cache {boltz_cache}"
    )

    # Fix 1: shared Numba cache so JIT compilation only happens once
    boltz_scratch = os.getenv("BOLTZ_SCRATCH_DIR", "/scratch/sea_boltz")
    numba_cache = f"{boltz_cache}/numba_cache"
    mpl_cache = f"{boltz_cache}/mpl_cache"
    run_path = f"{job_dir}/run.sh"
    if os.path.exists(run_path):
        os.remove(run_path)
    cuda_device = os.getenv("BOLTZ_CUDA_DEVICE", "0")
    with open(run_path, "w") as f:
        f.write(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            f"mkdir -p {numba_cache} {mpl_cache}\n"
            f"export HOME={os.path.abspath(job_dir)}\n"
            f"export CUDA_VISIBLE_DEVICES={cuda_device}\n"
            f"export NUMBA_CACHE_DIR={numba_cache}\n"
            f"export MPLCONFIGDIR={mpl_cache}\n"
            "export LD_LIBRARY_PATH=/nfs/home/khtang/.conda/envs/sea25/lib\n"
            f"{cmd}\n"
        )
    os.chmod(run_path, 0o755)

    # Pre-assign task UUID so we only need one locked write (no second save needed)
    task_uuid = str(uuid.uuid4())
    with _boltz_jobs_lock(jobs_file):
        # Re-check de-dup inside lock to close the TOCTOU window
        existing = _load_boltz_jobs(jobs_file)
        if job_name in existing and existing[job_name].get("status") not in _TERMINAL_STATES:
            return {"status": "already_running", "job_key": job_name}
        existing[job_name] = {
            "celery_task_id": task_uuid,
            "chembl_id": chembl_id,
            "ligand": ligand_name,
            "status": "queued",
            "queued_at": datetime.utcnow().isoformat(),
            "msa_cache_targets": msa_cache_targets,
        }
        _save_boltz_jobs(jobs_file, existing)

    run_boltz_job.apply_async(
        args=[task_id, job_name, boltz_dir, run_path],
        queue="boltz",
        task_id=task_uuid,
    )
    return {"status": "queued", "job_key": job_name}


@router.get("/boltz_job_status", dependencies=[Depends(_require_internal)])
def boltz_job_status(request: Request):
    task_id = request.query_params.get("task_id")
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id required")

    boltz_dir = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/boltz_jobs"
    jobs_file = f"{boltz_dir}/.boltz_jobs.json"
    if not os.path.exists(jobs_file):
        return {}

    jobs = _load_boltz_jobs(jobs_file)
    out = {}
    for job_key, info in jobs.items():
        out[job_key] = {
            "task_id": info.get("celery_task_id"),
            "chembl_id": info.get("chembl_id", job_key),
            "ligand": info.get("ligand"),
            "status": info.get("status", "unknown"),
        }
    return out


@router.get("/boltz_download", dependencies=[Depends(_require_internal)])
def boltz_download(request: Request):
    task_id = request.query_params.get("task_id")
    chembl_id = request.query_params.get("chembl_id")
    ligand = request.query_params.get("ligand")
    ligands_param = request.query_params.get("ligands")
    include_chimerax = request.query_params.get("include_chimerax_script", "false").lower() == "true"
    if not task_id or not chembl_id:
        raise HTTPException(status_code=400, detail="task_id and chembl_id required")

    if ligands_param:
        ligand_filter = [l.strip() for l in ligands_param.split(",") if l.strip()]
    elif ligand:
        ligand_filter = [ligand]
    else:
        ligand_filter = None

    boltz_dir = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/boltz_jobs"
    job_names = []

    manifest_path = f"{boltz_dir}/boltz_manifest.tsv"
    if os.path.exists(manifest_path):
        manifest = pd.read_csv(manifest_path, sep="\t")
        rows = manifest[manifest["CHEMBL ID"] == chembl_id]
        if ligand_filter is not None:
            rows = rows[rows["Ligand"].isin(ligand_filter)]
        job_names.extend(rows["job"].tolist())

    jobs_file = f"{boltz_dir}/.boltz_jobs.json"
    if os.path.exists(jobs_file):
        boltz_jobs = _load_boltz_jobs(jobs_file)
        for jkey, info in boltz_jobs.items():
            if info.get("chembl_id") == chembl_id:
                if ligand_filter is None or info.get("ligand") in ligand_filter:
                    if jkey not in job_names:
                        job_names.append(jkey)

    if not job_names:
        raise HTTPException(
            status_code=404,
            detail=f"No Boltz results for {chembl_id}" + (f" / {ligands_param or ligand}" if (ligands_param or ligand) else ""),
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for job_name in job_names:
            pred_dir = os.path.join(
                boltz_dir, job_name, "boltz_out",
                f"boltz_results_{job_name}", "predictions", job_name,
            )
            if not os.path.isdir(pred_dir):
                continue
            for fpath in glob.glob(os.path.join(pred_dir, "**", "*"), recursive=True):
                if os.path.isfile(fpath):
                    zf.write(fpath, arcname=os.path.join(job_name, os.path.relpath(fpath, pred_dir)))

        if include_chimerax:
            cxc_lines = ["# Open structures for comparison in ChimeraX", ""]
            model_num = 1
            for job_name in job_names:
                pred_dir = os.path.join(
                    boltz_dir, job_name, "boltz_out",
                    f"boltz_results_{job_name}", "predictions", job_name,
                )
                if not os.path.isdir(pred_dir):
                    continue
                for cif_path in sorted(glob.glob(os.path.join(pred_dir, "*.cif"))):
                    cxc_lines.append(f"open {job_name}/{os.path.basename(cif_path)}")
                    model_num += 1
            if model_num > 2:
                cxc_lines += ["", f"matchmaker #2-{model_num - 1} to #1"]
            elif model_num == 2:
                cxc_lines += ["", "matchmaker #2 to #1"]
            zf.writestr("open_in_chimerax.cxc", "\n".join(cxc_lines))

    buf.seek(0)
    if ligands_param:
        filename = f"boltz_{task_id}_{chembl_id}_both.zip"
    elif ligand:
        filename = f"boltz_{task_id}_{chembl_id}_{ligand}.zip"
    else:
        filename = f"boltz_{task_id}_{chembl_id}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=\"{filename}\""},
    )
