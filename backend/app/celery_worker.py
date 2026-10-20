from celery import Celery, current_task, shared_task, chord, current_app, group
from celery.result import GroupResult
from celery.schedules import crontab
from celery.signals import worker_init
import glob
import time
import asyncio
import traceback
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import numpy as np
import redis
import pickle
from .helper import get_smiles_property, _mkdir_, remove_salt, known_test_tc, calc_tanimoto_sets, sea_score, save_progress, get_progress
from .scripts.generate_boltz_jobs import generate_jobs as _generate_boltz_jobs, write_yaml as _write_boltz_yaml, sanitize as _sanitize_boltz
from dotenv import load_dotenv, find_dotenv
import os

load_dotenv(find_dotenv())

P_VALUE_THRESHOLD = -np.log10(float(os.getenv('P_THRESHOLD', '0.01')))  # p-value cutoff for significance
HIGH_TC_THRESHOLD = float(os.getenv('HIGH_TC_THRESHOLD', 0.95))  # MaxTc cutoff for high-similarity hits

_acc2seq_cache: dict = {}  # loaded once at worker init; avoids re-reading the large TSV per job


def _load_acc2seq():
    data_folder = os.getenv("SEA_DATA_FOLDER", "")
    lookup_path = os.getenv(
        "SEA_UNIPROT_INFO_FILE",
        os.path.join(data_folder, "uniprot_chembl_id_uniprot_info.tsv"),
    )
    if not os.path.exists(lookup_path):
        return
    try:
        df = pd.read_csv(lookup_path, sep="\t", usecols=["uniprot_accession", "sequence"])
        _acc2seq_cache.update(zip(df["uniprot_accession"].astype(str), df["sequence"].astype(str)))
        print(f"Loaded acc2seq lookup: {len(_acc2seq_cache)} entries")
    except Exception as e:
        print(f"Warning: could not load acc2seq lookup: {e}")


celery = Celery(
    "sea-worker",
    broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6380/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6380/1")
)
celery.autodiscover_tasks()

# Redis cache for target DataFrames (using separate DB to avoid conflicts)
redis_cache = redis.Redis(
    host=os.getenv("CELERY_BROKER_URL", "redis://localhost:6380/0").split("://")[1].split(":")[0],
    port=int(os.getenv("CELERY_BROKER_URL", "redis://localhost:6380/0").split(":")[2].split("/")[0]),
    db=2,  # Use DB 2 for caching (DB 0 = broker, DB 1 = backend)
    decode_responses=False  # Keep binary for pickle
)

r = redis.from_url(os.getenv("CELERY_BROKER_URL", "redis://redis:6380/0"),
                    db=0)


class RateLimitExceeded(Exception):
    pass


def _pick_cuda_device(job_name: str) -> str:
    devices_str = os.getenv("BOLTZ_CUDA_DEVICES") or os.getenv("BOLTZ_CUDA_DEVICE", "0")
    devices = [d.strip() for d in devices_str.split(",") if d.strip()]
    return devices[hash(job_name) % len(devices)]


def acquire_job_slot(task_id):
    """Acquire an active job slot. Raises RateLimitExceeded if no slots available."""
    key = "active:jobs"
    max_jobs = int(os.getenv('MAX_QUEUE_SIZE', '1'))

    if r.scard(key) >= max_jobs:
        raise RateLimitExceeded("Rate limit exceeded, queue is full. Please try again later.")

    r.sadd(key, task_id)
    r.expire(key, 3600)  # safety TTL to prevent permanent locks


def release_job_slot(task_id):
    """Release an active job slot."""
    r.srem("active:jobs", task_id)


def get_next_sea_slot(num_slots: int) -> int:
    """Return the next slot index using a Redis round-robin counter.

    Guarantees that consecutive job submissions land on different slot queues
    (up to num_slots concurrent jobs), avoiding hash-collision FIFO starvation.
    """
    return (int(r.incr("sea:slot_rr")) - 1) % num_slots


_RESULT_CACHE_TTL = 96 * 3600  # matches job-dir expiry in cleanup_task


def get_cached_job_id(input_hash: str):
    val = r.get(f"sea:result_cache:{input_hash}")
    return val.decode() if val else None


def set_cached_job_id(input_hash: str, task_id: str):
    r.setex(f"sea:result_cache:{input_hash}", _RESULT_CACHE_TTL, task_id)


def refresh_cached_job_ttl(input_hash: str):
    r.expire(f"sea:result_cache:{input_hash}", _RESULT_CACHE_TTL)


@worker_init.connect
def on_worker_init(**kwargs):
    """Start cache warming in the background so the worker is not blocked on NFS."""
    import threading
    threading.Thread(target=warm_target_cache, daemon=True).start()
    threading.Thread(target=_kill_orphaned_boltz_jobs, daemon=True).start()
    threading.Thread(target=_load_acc2seq, daemon=True).start()


def _kill_orphaned_boltz_jobs():
    """Mark any boltz jobs left in 'running' state as 'killed' (worker was restarted mid-job)."""
    import json as _json, glob as _glob, tempfile as _tempfile, fcntl as _fcntl
    from datetime import datetime, timezone
    job_dir = os.getenv("SEA_JOB_DIR", "")
    if not job_dir:
        return
    pattern = os.path.join(job_dir, "*", "boltz_jobs", ".boltz_jobs.json")
    for jobs_file in _glob.glob(pattern):
        try:
            lock_path = jobs_file + ".lock"
            with open(lock_path, "a") as _lf:
                _fcntl.lockf(_lf, _fcntl.LOCK_EX)
                try:
                    with open(jobs_file) as _f:
                        jobs = _json.load(_f)
                    changed = False
                    for job in jobs.values():
                        if job.get("status") == "running":
                            job["status"] = "killed"
                            job["finished_at"] = datetime.now(timezone.utc).isoformat()
                            changed = True
                    if changed:
                        dir_name = os.path.dirname(jobs_file)
                        with _tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, suffix=".tmp") as tf:
                            _json.dump(jobs, tf)
                            tmp_path = tf.name
                        os.chmod(tmp_path, 0o644)
                        os.replace(tmp_path, jobs_file)
                        print(f"boltz cleanup: marked orphaned jobs as killed in {jobs_file}")
                finally:
                    _fcntl.lockf(_lf, _fcntl.LOCK_UN)
        except Exception as e:
            print(f"boltz cleanup: failed to clean {jobs_file}: {e}")

def get_cached_target(target_file):
    """Get target DataFrame from Redis cache, or load from disk if not cached."""
    cache_key = f"target:{target_file}"

    # Try to get from cache
    cached_data = redis_cache.get(cache_key)
    if cached_data:
        return pickle.loads(cached_data)

    # Cache miss - load from disk
    try:
        df = pd.read_csv(target_file, sep="\t", index_col=0)
        # Store in cache (with 24 hour expiration)
        redis_cache.setex(cache_key, 86400, pickle.dumps(df))
        print(f"Cache miss - loaded and cached: {os.path.basename(target_file)}")
        return df
    except Exception as e:
        print(f"Error loading target file {target_file}: {e}")
        raise

@celery.task(name="sea.warm_cache")
def warm_target_cache():
    """Pre-populate Redis cache with all target files."""
    data_folder = os.getenv("SEA_DATA_FOLDER")
    if not data_folder:
        print("warm_target_cache: SEA_DATA_FOLDER not set, skipping")
        return {"total_files": 0, "cached": 0, "errors": 0}
    if not os.path.isdir(data_folder):
        print(f"warm_target_cache: SEA_DATA_FOLDER does not exist: {data_folder}, skipping")
        return {"total_files": 0, "cached": 0, "errors": 0}
    print(f"Warming Redis cache from {data_folder}...")

    # Find all .tsv files
    target_files = []
    for folder in os.listdir(data_folder):
        folder_path = os.path.join(data_folder, folder)
        if os.path.isdir(folder_path):
            target_files.extend(glob.glob(os.path.join(folder_path, "*.tsv")))

    cached_count = 0
    error_count = 0

    for i, target_file in enumerate(target_files):
        try:
            cache_key = f"target:{target_file}"
            # Skip if already cached
            if redis_cache.exists(cache_key):
                cached_count += 1
                continue

            # Load and cache
            df = pd.read_csv(target_file, sep="\t", index_col=0)
            redis_cache.setex(cache_key, 86400, pickle.dumps(df))
            cached_count += 1

            if (i + 1) % 100 == 0:
                print(f"Cached {i + 1}/{len(target_files)} files...")
        except Exception as e:
            print(f"Error caching {target_file}: {e}")
            error_count += 1

    result = {
        "total_files": len(target_files),
        "cached": cached_count,
        "errors": error_count
    }
    print(f"Cache warming complete: {result}")
    return result

@shared_task( name="sea.sea_target_score")
def sea_target_score(input, target, parent_task_id, total_targets,
                        tc_cutoff = 0.57,
                        mean_para = 4.24 * 10 ** -4,
                        std_coef = 4.49 * 10 ** -3,
		                std_exp = 0.665,
                        radius = 2,
                        fpSize = 2048,
                        useFeatures = False):
    
    try:
        data_test, mol_test = get_smiles_property(input)
        # Get target data from Redis cache (falls back to disk on cache miss)
        known = get_cached_target(target)
        # Calculate similarity - returns numpy array
        similarity = known_test_tc(known, data_test, fpSize)
        # Pass numpy array directly to sea_score (no DataFrame creation needed)
        z_score, p_value = sea_score(similarity, tc_cutoff, mean_para, std_coef, std_exp)
        # More efficient target name extraction
        target_name = known["target_name"].iloc[0]
        # update progress in Redis for this target
        completed = r.incr(f"progress:{parent_task_id}:completed_cnt")
        progress = int((completed / total_targets) * 100)
        is_done = completed >= total_targets

        celery.backend.store_result(
            parent_task_id,
            result={
                'completed_cnt': completed,
                'total': total_targets,
                'progress': progress,
                'status': 'POSTPROCESSING' if is_done else 'RUNNING',
            },
            state='POSTPROCESSING' if is_done else 'RUNNING'
        )
        if is_done:
            r.delete(f"progress:{parent_task_id}:completed_cnt")  
        print(f"Completed SEA score for target: {target_name}, Z-score: {z_score:.2f}, p-value: {p_value:.4e}")
    except Exception as e:
        print(f"Error processing target {target}: {e}")
        target_name = os.path.basename(target).split(".")[0]
        z_score, p_value = None, None
        # TODO : fail progress update for this target
    return target_name, z_score, p_value


@shared_task(name="sea.sea_target_score_batch")
def sea_target_score_batch(targets, parent_task_id, total_targets, smiles_key,
                           fp_key=None,
                           tc_cutoff=0.29, mean_slope=4.3304e-04,
                           mean_intercept=-0.01631567,
                           std_a=0.0047, std_b=0.6409, std_c=0.4999,
                           gev_c=0.0, gev_loc=0.0, gev_scale=1.0,
                           p_threshold=0.01, high_tc_threshold=0.95,
                           radius=2, fpSize=2048, useFeatures=False):
    """Process a batch of targets in a single Celery task."""
    fp_cols_str = np.arange(fpSize).astype(str)

    # Load pre-computed fingerprints if available, otherwise compute from SMILES
    fp_raw = redis_cache.get(fp_key) if fp_key else None
    if fp_raw is not None:
        fp_test = pickle.loads(fp_raw)
    else:
        raw = redis_cache.get(smiles_key)
        if raw is None:
            raise ValueError(f"Input data not found in Redis for key: {smiles_key}")
        smiles_test = pickle.loads(raw)
        data_test, _ = get_smiles_property(smiles_test)
        fp_test = data_test[np.arange(fpSize)].to_numpy()

    results = []
    for target in targets:
        try:
            known      = get_cached_target(target)
            fp_known   = known[fp_cols_str].to_numpy()
            similarity = calc_tanimoto_sets(fp_known, fp_test)

            best_tc = float(np.max(similarity)) if similarity.size > 0 else 0.0

            z_score, p_value, log_sf, raw_score, size = sea_score(
                similarity, tc_cutoff, mean_slope, mean_intercept,
                std_a, std_b, std_c,
                gev_c=gev_c, gev_loc=gev_loc, gev_scale=gev_scale)
            target_name = known["target_name"].iloc[0]
            uniprot_id = known["uniprot_id"].iloc[0] if "uniprot_id" in known.columns else None
            uniprot_accession = known["target_accession"].iloc[0] if "target_accession" in known.columns else None
            results.append((target_name, z_score, p_value, log_sf, best_tc, uniprot_id, uniprot_accession))
        except Exception as e:
            print(f"Error processing target {target}: {e}")
            target_name = os.path.basename(target).split(".")[0]
            results.append((target_name, None, None, None, None, None, None))

        # Update progress atomically via Redis
        completed = r.incr(f"progress:{parent_task_id}:completed_cnt")
        progress = int((completed / total_targets) * 100)
        is_done = completed >= total_targets

        # Only write to Celery backend when the integer-percent progress changes (or on completion)
        # to reduce Redis round-trips from N_targets down to at most 100 per job.
        prev_progress = int(((completed - 1) / total_targets) * 100)
        if progress != prev_progress or is_done:
            celery.backend.store_result(
                parent_task_id,
                result={
                    'completed_cnt': completed,
                    'total': total_targets,
                    'progress': progress,
                    'status': 'POSTPROCESSING' if is_done else 'RUNNING',
                },
                state='POSTPROCESSING' if is_done else 'RUNNING'
            )
        if is_done:
            r.delete(f"progress:{parent_task_id}:completed_cnt")
    return results


def _write_log_(log_file, message):
    """Append a timestamped line to a log file."""
    from datetime import datetime
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    with open(log_file, "a") as f:
        f.write(f"[{timestamp}] {message}\n")


@celery.task(name="sea.postprocess_sea_job")
def postprocess_sea_job(group_results, task_id, list_chembl, smiles_test):
    """Chord callback: runs on a Celery worker after all sea_target_score tasks complete."""
    import json

    # Flatten batch results: each element is a list of (name, z, p) tuples
    flat_results = []
    for batch_result in group_results:
        if isinstance(batch_result, list):
            flat_results.extend(batch_result)
        else:
            flat_results.append(batch_result)
    group_results = flat_results

    # Clean up shared input from Redis
    redis_cache.delete(f"input:{task_id}")

    job_dir = os.environ.get("SEA_JOB_DIR")
    data_folder = os.environ.get("SEA_DATA_FOLDER")
    save_folder = f"{job_dir}/{task_id}/sea_output"
    log_dir = f"{job_dir}/{task_id}/logs"

    if not os.path.exists(save_folder):
        _mkdir_(save_folder)
    if not os.path.exists(log_dir):
        _mkdir_(log_dir)

    phase1_log = f"{log_dir}/sea_results.log"
    phase2_log = f"{log_dir}/similarity.log"
    phase3_log = f"{log_dir}/heatmap.log"

    _write_log_(phase1_log, f"Post-processing started for task {task_id}")
    _write_log_(phase1_log, f"Total targets received: {len(group_results)}")

    # --- Phase 1: Aggregate SEA results ---
    try:
        _write_log_(phase1_log, "Phase 1 started: aggregating SEA results")
        save_progress(task_id, {
            'progress': 100, 'status': 'RUNNING', 'error': None,
            'sea_calculated': False, 'best_similarity_calculated': False,
            'heatmap_calculated': False, 'failed': False,
        })

        # Pair results with their target files and filter out failures
        valid = [
            (name, z, p, log_sf, chembl_path, tc, uid, uacc)
            for (name, z, p, log_sf, tc, uid, uacc), chembl_path in zip(group_results, list_chembl)
            if z is not None and log_sf is not None
        ]
        n_failed = len(group_results) - len(valid)
        _write_log_(phase1_log, f"Scored targets: {len(valid)} valid, {n_failed} failed (z/p-value is None)")
        if not valid:
            _write_log_(phase1_log, "Phase 1 FAILED: all target scores returned None")
            save_progress(task_id, {
                'progress': 100, 'status': 'ERROR', 'error': 'All target scores failed',
                'sea_calculated': False, 'best_similarity_calculated': False,
                'heatmap_calculated': False, 'failed': True,
            })
            release_job_slot(task_id)
            return {"task_id": task_id, "status": "ERROR"}

        target_name_list, z_score_summary, p_value_summary, log_sf_summary, valid_chembl, best_tc_summary, uniprot_id_list, uniprot_accession_list = zip(*valid)

        list_chembl_names = []
        for item in valid_chembl:
            target_name_with_species = "/".join(item.split('.')[0].split("/")[-2:])
            list_chembl_names.append(target_name_with_species)

        sea_result = pd.DataFrame()
        sea_result["Target_Chembl_ID"] = [x.split('/')[1] for x in list_chembl_names]
        sea_result["Target_Species"] = [x.split('/')[0] for x in list_chembl_names]
        sea_result["Z_score"] = np.round(z_score_summary, 2)
        sea_result["p_value"] = list(p_value_summary)
        sea_result["p(P_value)"] = np.round(-np.array(log_sf_summary) / np.log(10), 4)
        sea_result["Target_Name"] = target_name_list
        sea_result['MaxTc'] = np.round(best_tc_summary, 2)
        sea_result["Uniprot_ID"] = list(uniprot_id_list)
        sea_result["Uniprot_Accession"] = list(uniprot_accession_list)

        # Look up protein sequences from in-memory cache (loaded once at worker init)
        sea_result["Sequence"] = sea_result["Uniprot_Accession"].map(_acc2seq_cache).fillna("")

        n_before_filter = len(sea_result)
        filtered_result = sea_result[(sea_result["p(P_value)"] >= P_VALUE_THRESHOLD) | (sea_result["MaxTc"] > HIGH_TC_THRESHOLD)]

        # Format p_value as scientific notation for output files
        sea_result["p_value"] = sea_result["p_value"].apply(lambda v: f"{v:.4e}")
        filtered_result = filtered_result.copy()
        filtered_result["p_value"] = filtered_result["p_value"].apply(lambda v: f"{v:.4e}")

        # Sort by p_value ascending (small to large) using numeric p(P_value) descending
        sea_result = sea_result.sort_values(by="p(P_value)", ascending=False, ignore_index=True)
        filtered_result = filtered_result.sort_values(by="p(P_value)", ascending=False, ignore_index=True)

        output_cols = [c for c in sea_result.columns if c != "Target_Species"]
        sea_result[output_cols].to_csv(f"{save_folder}/sea_result_all.tsv", sep="\t", index=False)
        _write_log_(phase1_log, f"Significant hits ): {len(filtered_result)} of {n_before_filter}")
        filtered_result[output_cols].to_csv(f"{save_folder}/sea_result.tsv", sep="\t", index=False)

        _write_log_(phase1_log, "Phase 1 completed: sea_result.tsv written")
        save_progress(task_id, {
            'progress': 100, 'status': 'RUNNING', 'error': None,
            'sea_calculated': True, 'best_similarity_calculated': False,
            'heatmap_calculated': False, 'failed': False,
        })
    except Exception as e:
        _write_log_(phase1_log, f"Phase 1 FAILED: {e}\n{traceback.format_exc()}")
        print(f"Error aggregating SEA results: {e}")
        save_progress(task_id, {
            'progress': 100, 'status': 'ERROR', 'error': str(e),
            'sea_calculated': False, 'best_similarity_calculated': False,
            'heatmap_calculated': False, 'failed': True,
        })
        release_job_slot(task_id)
        raise

    # --- Phases 2–3 run in background so SEA results are immediately visible ---
    def _run_phases_2_3():
        # Phase 2: Similarity scores for each hit target
        try:
            n_hit_targets = filtered_result.shape[0]
            _write_log_(phase2_log, f"Phase 2 started: computing similarity scores for {n_hit_targets} hit targets")
            data_test, mol_test = get_smiles_property(smiles_test)
            name_to_smiles = dict(zip(data_test["Name"], data_test["SMILES"]))
            test_names = data_test["Name"].tolist()
            n_test = len(test_names)
            _write_log_(phase2_log, f"Query compounds: {n_test} ({', '.join(test_names)})")

            log_lock = threading.Lock()

            fp_cols_str_p2 = np.arange(2048).astype(str)
            fp_test_p2     = data_test[np.arange(2048)].to_numpy()

            def _process_one_target_(idx):
                row = filtered_result.iloc[idx]
                target_chembl_id = row["Target_Chembl_ID"]
                target_uniprot_id = row["Uniprot_ID"]
                species = row["Target_Species"]
                known      = get_cached_target(f"{data_folder}/{species}/{target_chembl_id}.tsv")
                fp_known   = known[fp_cols_str_p2].to_numpy()
                similarity = calc_tanimoto_sets(fp_known, fp_test_p2)
                sim_rounded = np.round(similarity, 4)
                known_ids = known["molecule_chembl_id"].tolist()
                known_smiles = dict(zip(known["molecule_chembl_id"], known["canonical_smiles"]))
                target_cid = known["target_chembl_id"].iloc[0]
                target_nm = known["target_name"].iloc[0]

                best_idx = np.argmax(sim_rounded, axis=0)
                best_tc_vals = np.max(sim_rounded, axis=0)
                if np.ndim(best_idx) == 0:
                    best_idx = np.array([int(best_idx)])
                    best_tc_vals = np.array([float(best_tc_vals)])
                best_known_ids = [known_ids[i] for i in best_idx]

                best_similarity = pd.DataFrame({
                    "Name": test_names,
                    "SMILES": data_test["SMILES"].tolist(),
                    "Target_Chembl_Id": target_cid,
                    "Target_Name": target_nm,
                    "Uniprot_ID": row["Uniprot_ID"],
                    "Most_similar_known": best_known_ids,
                    "Tanimoto_Coefficient": best_tc_vals,
                    "Known_Hit_SMILES": [known_smiles[kid] for kid in best_known_ids],
                })
                best_similarity = best_similarity.sort_values(by=["Tanimoto_Coefficient"], ascending=False, ignore_index=True)
                best_similarity = best_similarity[best_similarity["Tanimoto_Coefficient"] > HIGH_TC_THRESHOLD]
                best_similarity.to_csv(f"{save_folder}/best_similar_{target_cid}.tsv", sep="\t", index=False)

                k = min(10, sim_rounded.shape[0])
                summary_tc, summary_ref, summary_known = [], [], []
                summary_ref_smiles, summary_known_smiles = [], []
                for idxC in range(n_test):
                    col_vals = sim_rounded[:, idxC] if sim_rounded.ndim > 1 else sim_rounded
                    top_k_idx = np.argpartition(col_vals, -k)[-k:] if k < len(col_vals) else np.arange(len(col_vals))
                    top_k_idx = top_k_idx[np.argsort(col_vals[top_k_idx])[::-1]]
                    top_ids = [known_ids[i] for i in top_k_idx]
                    summary_tc += col_vals[top_k_idx].tolist()
                    summary_ref += [test_names[idxC]] * len(top_k_idx)
                    summary_known += top_ids
                    summary_ref_smiles += [name_to_smiles[test_names[idxC]]] * len(top_k_idx)
                    summary_known_smiles += [known_smiles[kid] for kid in top_ids]

                sim_df = pd.DataFrame({
                    "Name": summary_ref,
                    "Tanimoto_Score": summary_tc,
                    "SMILES": summary_ref_smiles,
                    "Known_Hit_ID": summary_known,
                    "Known_Hit_SMILES": summary_known_smiles,
                })
                sim_df = sim_df.sort_values(by=["Name", "Tanimoto_Score"], ascending=[True, False], ignore_index=True)
                sim_df.to_csv(f"{save_folder}/top10_{target_cid}.tsv", sep="\t", index=False)
                sim_df["Target_ChEMBL_ID"] = target_cid
                sim_df["Target_Name"] = target_nm
                sim_df["Uniprot_ID"] = target_uniprot_id

                with log_lock:
                    _write_log_(phase2_log, f"[{idx+1}/{n_hit_targets}] {target_cid} ({target_nm}): known={len(known_ids)}, max_tc={float(best_tc_vals.max()):.4f}, high_sim_hits={len(best_similarity)}")

                return sim_df.to_dict(orient="records")

            heatmap_data = []
            phase2_errors = 0
            n_workers = int(os.getenv("PHASE2_WORKERS", "20"))
            with ThreadPoolExecutor(max_workers=n_workers) as executor:
                futures = {executor.submit(_process_one_target_, idx): idx for idx in range(n_hit_targets)}
                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        heatmap_data.extend(future.result())
                    except Exception as e:
                        phase2_errors += 1
                        target_chembl_id = filtered_result["Target_Chembl_ID"].iloc[idx]
                        with log_lock:
                            _write_log_(phase2_log, f"[{idx+1}/{n_hit_targets}] Target error ({target_chembl_id}): {e}\n{traceback.format_exc()}")
                        print(f"Error calculating similarity for target {target_chembl_id}: {e}")

            _write_log_(phase2_log, f"Phase 2 completed: {n_hit_targets - phase2_errors}/{n_hit_targets} targets processed, {phase2_errors} errors")
        except Exception as e:
            _write_log_(phase2_log, f"Phase 2 FAILED: {e}\n{traceback.format_exc()}")
            print(f"Error in similarity calculation: {e}")
            return

        # Phase 3: Heatmap data
        try:
            _write_log_(phase3_log, f"Phase 3 started: building heatmap from {len(heatmap_data)} similarity records")
            group_fields = {"Name", "SMILES"}
            grouped_dict = {}
            for item in heatmap_data:
                key = (item["Name"], item["SMILES"])
                if key not in grouped_dict:
                    grouped_dict[key] = []
                hit = {k: item[k] for k in item if k not in group_fields}
                grouped_dict[key].append(hit)

            heatmap_result = []
            for (name, smiles), hits in grouped_dict.items():
                grouped_target_hits = {}
                for hit in hits:
                    tkey = f"{hit['Target_ChEMBL_ID']}:{hit['Uniprot_ID']}:{hit['Target_Name']}"
                    if tkey not in grouped_target_hits:
                        grouped_target_hits[tkey] = []
                    grouped_target_hits[tkey].append({
                        "Known_Hit_ID": hit["Known_Hit_ID"],
                        "Known_Hit_SMILES": hit["Known_Hit_SMILES"],
                        "Tanimoto_Score": hit["Tanimoto_Score"]
                    })
                heatmap_result.append({"Name": name, "SMILES": smiles, "hits": grouped_target_hits})

            heatmap_file = f"{job_dir}/{task_id}/.heatmap"
            with open(heatmap_file, "w") as hm:
                json.dump(heatmap_result, hm, indent=4)
        except Exception as e:
            _write_log_(phase3_log, f"Phase 3 FAILED: {e}\n{traceback.format_exc()}")
            print(f"Error generating heatmap: {e}")
            return

        _write_log_(phase3_log, f"Phase 3 completed: {len(heatmap_result)} compounds written to .heatmap")
        save_progress(task_id, {
            'progress': 100, 'status': 'SUCCESS', 'error': None,
            'sea_calculated': True, 'best_similarity_calculated': True,
            'heatmap_calculated': True, 'failed': False,
        })

    threading.Thread(target=_run_phases_2_3, daemon=True).start()

    # Phase 4: Boltz-2 (synchronous — finishes before SUCCESS so boltz_status is populated on first poll)
    phase4_log = f"{log_dir}/boltz.log"
    try:
        import csv, hashlib, tempfile as _tempfile

        if len(smiles_test) > 1:
            _write_log_(phase4_log, "Phase 4: skipping Boltz2 auto-queue (multi-molecule submission)")
        else:
            _sea_result_file = f"{save_folder}/sea_result.tsv"
            with open(_sea_result_file) as _f:
                _rows = [r for r in csv.DictReader(_f, delimiter='\t') if float(r['MaxTc']) < 1.0]
            _rows.sort(key=lambda r: float(r['p_value']))
            _rows = _rows[:5]

            if not _rows:
                _write_log_(phase4_log, "Phase 4: no novel targets (MaxTc < 1.0) found, skipping")
            else:
                boltz_dir = f"{job_dir}/{task_id}/boltz_jobs"
                os.makedirs(boltz_dir, exist_ok=True)
                _msa_cache_root = os.getenv("BOLTZ_MSA_CACHE_DIR",
                    os.path.join(os.getenv("SEA_JOB_DIR", "/tmp"), "msa_cache"))
                boltz_bin   = os.getenv("BOLTZ_BIN", "boltz")
                boltz_cache = os.getenv("BOLTZ_CACHE_DIR", "/scratch/sea_boltz")
                boltz_jobs  = {}
                pending_dispatches = []

                for _target in _rows:
                    target_id = _target['Target_Chembl_ID']
                    accession = _target['Uniprot_Accession']
                    sequence  = _acc2seq_cache.get(accession)
                    if not sequence:
                        _write_log_(phase4_log, f"Phase 4: no sequence for {accession}, skipping")
                        continue

                    _msa_path = os.path.join(_msa_cache_root,
                        f"{hashlib.sha256(sequence.encode()).hexdigest()[:16]}.a3m")
                    _msa_hit = os.path.exists(_msa_path)
                    chains   = [{"sequence": sequence, "label": accession}]

                    for entry in smiles_test:
                        _parts    = entry.split()
                        _smiles   = _parts[0]
                        _cname    = _parts[1] if len(_parts) > 1 else _parts[0]
                        safe_name = _sanitize_boltz(_cname)
                        job_name  = f"{target_id}__{safe_name}"
                        jdir      = f"{boltz_dir}/{job_name}"
                        os.makedirs(jdir, exist_ok=True)

                        if _msa_hit:
                            precomp = os.path.join(jdir, "msa_precomp")
                            os.makedirs(precomp, exist_ok=True)
                            link = os.path.join(precomp, f"{_sanitize_boltz(accession)}.a3m")
                            if not os.path.exists(link):
                                os.symlink(_msa_path, link)
                            msa_mode, msa_dir_arg, msa_flag = "precomputed", precomp, ""
                        else:
                            msa_mode, msa_dir_arg, msa_flag = "server", "", " --use_msa_server"

                        yaml_path = f"{jdir}/{job_name}.yaml"
                        _write_boltz_yaml(yaml_path, chains, _smiles, msa_mode, msa_dir_arg, True)

                        run_sh_path = f"{jdir}/run.sh"
                        with open(run_sh_path, "w") as _f:
                            _f.write(
                                f"#!/bin/bash\n"
                                f"export CUDA_VISIBLE_DEVICES={_pick_cuda_device(job_name)}\n"
                                f"export NUMBA_DISABLE_JIT=1\n"
                                f"export NUMBA_CACHE_DIR=/tmp/numba_cache\n"
                                f"export MPLCONFIGDIR=/tmp/matplotlib\n"
                                f"export XDG_CONFIG_HOME=/tmp/xdg_config\n"
                                f"export TRITON_CACHE_DIR=/tmp/triton_cache\n"
                                f"{boltz_bin} predict {os.path.abspath(yaml_path)}"
                                f" --out_dir {os.path.abspath(jdir)}/boltz_out"
                                f"{msa_flag} --cache {boltz_cache}\n"
                            )
                        os.chmod(run_sh_path, 0o755)

                        boltz_jobs[job_name] = {
                            "status": "queued",
                            "celery_task_id": None,
                            "chembl_id": target_id,
                            "ligand": _cname,
                        }
                        pending_dispatches.append((job_name, run_sh_path))

                if boltz_jobs:
                    jobs_file = f"{boltz_dir}/.boltz_jobs.json"
                    with _tempfile.NamedTemporaryFile("w", dir=boltz_dir, delete=False, suffix=".tmp") as _tf:
                        json.dump(boltz_jobs, _tf)
                        _tmp = _tf.name
                    os.replace(_tmp, jobs_file)

                    # Respect global Boltz cap — count active non-terminal jobs
                    # Exclude the current task's file (just written above) to avoid
                    # counting our own pending jobs against the available slots.
                    _terminal = {"done", "failed"}
                    _max_queued = int(os.getenv("BOLTZ_MAX_QUEUED", "10"))
                    _active_count = 0
                    for _jf in glob.glob(f"{job_dir}/*/boltz_jobs/.boltz_jobs.json"):
                        if f"/{task_id}/" in _jf:
                            continue
                        try:
                            with open(_jf) as _jfh:
                                _active_count += sum(1 for _j in json.load(_jfh).values() if _j.get("status") not in _terminal)
                        except Exception:
                            pass
                    _slots_available = max(0, _max_queued - _active_count)
                    if _slots_available == 0:
                        _write_log_(phase4_log, f"Phase 4: Boltz cap reached ({_active_count}/{_max_queued}), skipping auto-dispatch")
                    else:
                        pending_dispatches = pending_dispatches[:_slots_available]

                    if _slots_available > 0 and pending_dispatches:
                        _total_gpus = int(os.getenv("BOLTZ_NUM_GPUS", "4"))
                        _start_gpu = hash(task_id) % _total_gpus
                        _queue_depths = []
                        for _gi in range(_total_gpus):
                            _qname = f"boltz-gpu{_gi}"
                            _queued = int(r.llen(_qname) or 0)
                            _queue_depths.append(_queued)
                        _total_queued = sum(_queue_depths)
                        _free_gpus = sum(1 for d in _queue_depths if d == 0)
                        _n_dispatching = len(pending_dispatches)
                        if _free_gpus == 0:
                            _best = min(range(_total_gpus), key=lambda i: _queue_depths[i])
                            _gpu_queues = [f"boltz-gpu{_best}"]
                        elif _free_gpus >= _n_dispatching:
                            _free_indices = [i for i, d in enumerate(_queue_depths) if d == 0]
                            _gpu_queues = [
                                f"boltz-gpu{_free_indices[(_start_gpu + i) % len(_free_indices)]}"
                                for i in range(_n_dispatching)
                            ]
                        else:
                            _free_indices = [i for i, d in enumerate(_queue_depths) if d == 0]
                            _gpu_queues = [
                                f"boltz-gpu{_free_indices[i % len(_free_indices)]}"
                                for i in range(_n_dispatching)
                            ]

                        for i, (job_name, run_sh_path) in enumerate(pending_dispatches):
                            _queue = _gpu_queues[i % len(_gpu_queues)]
                            _task = run_boltz_job.apply_async(
                                args=[task_id, job_name, boltz_dir, run_sh_path],
                                queue=_queue,
                            )
                            boltz_jobs[job_name]["celery_task_id"] = _task.id

                        _write_log_(phase4_log,
                            f"Phase 4: queued {len(pending_dispatches)} Boltz job(s) across {len(_rows)} target(s) "
                            f"using {len(set(_gpu_queues))} GPU(s) "
                            f"(free={_free_gpus}/{_total_gpus}, queued_boltz={_total_queued}, "
                            f"cap={_active_count + len(pending_dispatches)}/{_max_queued})")

    except Exception as e:
        _write_log_(phase4_log, f"Phase 4 error: {e}")

    save_progress(task_id, {
        'progress': 100, 'status': 'SUCCESS', 'error': None,
        'sea_calculated': True, 'best_similarity_calculated': False,
        'heatmap_calculated': False, 'failed': False,
    })
    print(f"Post-processing completed for task {task_id}")
    release_job_slot(task_id)
    return {"task_id": task_id, "status": "SUCCESS"}



@shared_task(name="sea.similarity_task")
def sea_similarity_score( input, target_chembl_id, save_folder,
                        tc_cutoff = 0.57, 
                        mean_para = 4.24 * 10 ** -4, 
                        std_coef = 4.49 * 10 ** -3, 
		                std_exp = 0.665, 
                        radius = 2, 
                        fpSize = 2048, 
                        useFeatures = False):
    data_test, mol_test = get_smiles_property(input)
    print("Calculating similarity for target:", target_chembl_id)
    known = pd.read_csv(f"{os.getenv("SEA_DATA_FOLDER")}/{target_chembl_id}.tsv", sep = "\t", index_col = 0)
    similarity = known_test_tc(known, data_test, fpSize)
    result = pd.DataFrame(np.round(similarity, 4), index = known["molecule_chembl_id"].tolist(), 
                          columns = data_test["Name"].tolist())
    result = result.T
    known.index = known["molecule_chembl_id"].tolist()
    best_similarity = pd.DataFrame()
    best_similarity["Name"] = list(result.index)
    best_similarity["SMILES"] = data_test["SMILES"].tolist()
    best_similarity["Target_Chembl_Id"] = [known["target_chembl_id"].tolist()[0]] * best_similarity.shape[0]
    best_similarity["Target_Name"] = [known["target_name"].tolist()[0]] * best_similarity.shape[0]
    best_similarity["Most_similar_known"] = list(result.idxmax(axis = "columns"))
    best_similarity["Tanimoto_Coefficient"] = list(result.max(axis = 1))
    best_similarity["Known_Hit_SMILES"] = [known.loc["{}".format(name), "canonical_smiles"] for name in result.idxmax(axis = "columns")] 
    best_similarity = best_similarity.sort_values(by = ["Tanimoto_Coefficient"], ascending = False, ignore_index = True)
    best_similarity = best_similarity[best_similarity["Tanimoto_Coefficient"] >= HIGH_TC_THRESHOLD]
    best_similarity.to_csv(f"{save_folder}/best_similar_{known["target_chembl_id"].tolist()[0]}.tsv", sep = "\t", index = False)
    
    # get top 10 similarity matrix
    summary_tc = []
    summary_ref = []
    summary_known = []
    summary_ref_smiles = []
    summary_known_smiles = []
    result = pd.DataFrame(np.round(similarity, 4), columns = data_test["Name"].tolist())
    ref_compound = list(result.columns)
    known_compound = list(result.index)
    result["Known"] = known["molecule_chembl_id"].tolist()
    for idxC in np.arange(result.shape[1] - 1) : 
        top10 = result.sort_values(by = ["{}".format(ref_compound[idxC])], ascending = False, ignore_index = True).head(10)
        summary_tc += top10["{}".format(ref_compound[idxC])].tolist() 
        summary_ref += [ref_compound[idxC]] * top10.shape[0] 
        summary_known += top10["Known"].tolist()
        summary_ref_smiles += [data_test["SMILES"].to_numpy()[np.where(data_test["Name"].to_numpy() == ref_compound[idxC])[0]][0]] * top10.shape[0] 
        summary_known_smiles += [known.loc["{}".format(name), "canonical_smiles"] for name in top10["Known"].tolist()]
    similarity_matrix = pd.DataFrame()
    similarity_matrix["Name"] = summary_ref
    similarity_matrix["Tanimoto_Score"] = summary_tc
    similarity_matrix["SMILES"] = summary_ref_smiles
    similarity_matrix["Known_Hit_ID"] = summary_known
    similarity_matrix["Known_Hit_SMILES"] = summary_known_smiles
    similarity_matrix = similarity_matrix.sort_values(by = ["Name", "Tanimoto_Score"], ascending = [True, False], ignore_index = True)
 
    similarity_matrix.to_csv(f"{save_folder}/top10_{known["target_chembl_id"].tolist()[0]}.tsv", sep = "\t", index = False)
    return "Similarity calculation completed for target: {}".format(target_chembl_id)




@celery.task(name="main.cleanup_task")
def cleanup_task(*args, **kwargs):
    import re, shutil, time

    save_folder = os.environ.get("SEA_JOB_DIR")
  
    _uuid_re = re.compile(
        r'^[0-9a-f]{12}$',
        re.IGNORECASE,
    )

    sea_dirs = [d for d in os.listdir(save_folder) if _uuid_re.match(d)]
    if len(sea_dirs) < 30:
        return "No cleanup needed. There are currently {} SEA job folders.".format(len(sea_dirs))

    removed = 0
    for name in sea_dirs:
        dir_path = os.path.join(save_folder, name)
        if os.path.isdir(dir_path):
            if (time.time() - os.path.getmtime(dir_path)) > 96 * 3600:
                shutil.rmtree(dir_path)
                removed += 1
                print(f"Removed old job folder: {dir_path}")
    return f"Cleanup completed. Removed {removed} folder(s)."



@celery.task(name="boltz.run_job", bind=True)
def run_boltz_job(self, task_id: str, job_key: str, boltz_dir: str, run_script: str):
    """Run a Boltz2 prediction job as a subprocess. Routes to the dedicated 'boltz' queue (concurrency=1)."""
    import subprocess, glob as _glob, json as _json
    from datetime import datetime, timezone
    jobs_file = f"{boltz_dir}/.boltz_jobs.json"
    log_file = os.path.join(boltz_dir, job_key, "boltz_run.log")

    def _now():
        return datetime.now(timezone.utc).isoformat()

    def _update_jobs(updates: dict):
        import tempfile as _tempfile, fcntl as _fcntl
        try:
            lock_path = jobs_file + ".lock"
            with open(lock_path, "a") as _lf:
                _fcntl.lockf(_lf, _fcntl.LOCK_EX)
                try:
                    with open(jobs_file) as _f:
                        jobs = _json.load(_f)
                    jobs[job_key].update(updates)
                    dir_name = os.path.dirname(jobs_file)
                    with _tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, suffix=".tmp") as tf:
                        _json.dump(jobs, tf)
                        tmp_path = tf.name
                    os.chmod(tmp_path, 0o644)
                    os.replace(tmp_path, jobs_file)
                finally:
                    _fcntl.lockf(_lf, _fcntl.LOCK_UN)
        except Exception as e:
            print(f"run_boltz_job: failed to update jobs file for {job_key}: {e}")

    # Mark running
    start_time = time.monotonic()
    _update_jobs({"status": "running", "celery_task_id": self.request.id,
                  "started_at": _now(), "log_file": log_file})

    # Stream subprocess output to log file and celery stdout
    success = False
    try:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        with open(log_file, "w") as lf:
            lf.write(f"[{_now()}] Starting {job_key}\n")
            lf.flush()
            proc = subprocess.Popen(
                ["bash", run_script],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            for line in proc.stdout:
                lf.write(line)
                lf.flush()
                print(f"run_boltz_job [{job_key}]: {line}", end="")
            proc.wait()
            success = proc.returncode == 0
            elapsed = time.monotonic() - start_time
            lf.write(f"[{_now()}] Process exited with code {proc.returncode} (elapsed: {elapsed:.1f}s)\n")
        if not success:
            print(f"run_boltz_job: {job_key} exited {proc.returncode}")
        else:
            print(f"run_boltz_job: {job_key} subprocess OK")
    except Exception as e:
        print(f"run_boltz_job: subprocess error for {job_key}: {e}")

    # Check output files as ground truth (boltz may exit non-zero but still produce output)
    pred_base = os.path.join(boltz_dir, job_key, "boltz_out",
                             f"boltz_results_{job_key}", "predictions", job_key)
    print(f"run_boltz_job: checking pred_base={pred_base} exists={os.path.isdir(pred_base)}")
    cif_files = _glob.glob(os.path.join(pred_base, "*.cif")) if os.path.isdir(pred_base) else []
    print(f"run_boltz_job: cif_files={cif_files}")
    if not success and cif_files:
        print(f"run_boltz_job: {job_key} non-zero exit but .cif files found — marking done")
        success = True

    elapsed = time.monotonic() - start_time
    final_status = "done" if success else "failed"
    _update_jobs({"status": final_status, "finished_at": _now(), "elapsed_seconds": round(elapsed, 1)})
    print(f"run_boltz_job: {job_key} → {final_status} in {elapsed:.1f}s")

    # Populate MSA cache for chains that were fetched via --use_msa_server
    if success:
        try:
            import hashlib as _hashlib, shutil as _shutil
            with open(jobs_file) as _f:
                _job_info = _json.load(_f).get(job_key, {})
            msa_cache_targets = _job_info.get("msa_cache_targets", [])
            if msa_cache_targets:
                cache_dir = os.getenv(
                    "BOLTZ_MSA_CACHE_DIR",
                    os.path.join(os.getenv("SEA_JOB_DIR", "/tmp"), "msa_cache"),
                )
                os.makedirs(cache_dir, exist_ok=True)
                msa_raw_dir = os.path.join(
                    boltz_dir, job_key, "boltz_out",
                    f"boltz_results_{job_key}", "msa",
                    f"{job_key}_unpaired_tmp_env",
                )
                uniref = os.path.join(msa_raw_dir, "uniref.a3m")
                bfd = os.path.join(msa_raw_dir, "bfd.mgnify30.metaeuk30.smag30.a3m")
                if os.path.exists(uniref):
                    # Merge uniref + bfd into a single a3m (standard ColabFold format)
                    merged_lines = []
                    for src in [uniref, bfd]:
                        if os.path.exists(src):
                            with open(src) as _f:
                                merged_lines.append(_f.read())
                    merged = "".join(merged_lines)
                    for target in msa_cache_targets:
                        dest = os.path.join(cache_dir, f"{target['seq_hash']}.a3m")
                        if not os.path.exists(dest):
                            with open(dest, "w") as _f:
                                _f.write(merged)
                            print(f"run_boltz_job: cached MSA for {target['label']} → {dest}")
        except Exception as e:
            print(f"run_boltz_job: MSA cache population failed for {job_key}: {e}")


celery.conf.beat_schedule = {
    'daily-cleanup-task': {
        'task': 'main.cleanup_task',
        'schedule': crontab(minute=15, hour='*'),  # Run every hour
    },
}

celery.conf.timezone = 'UTC'













