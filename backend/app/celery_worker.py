from celery import Celery, current_task, shared_task, chord, current_app, group
from celery.result import GroupResult
from celery.schedules import crontab
from celery.signals import worker_init
import glob
import time
import asyncio
import traceback
import pandas as pd
import numpy as np
import redis
import pickle
from .helper import get_smiles_property, _mkdir_, remove_salt, known_test_tc, sea_score, save_progress, get_progress
from .scripts.generate_boltz_jobs import generate_jobs as _generate_boltz_jobs
from dotenv import load_dotenv, find_dotenv
import os

load_dotenv(find_dotenv())


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


@worker_init.connect
def on_worker_init(**kwargs):
    """Start cache warming in the background so the worker is not blocked on NFS."""
    import threading
    threading.Thread(target=warm_target_cache, daemon=True).start()

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
                           tc_cutoff=0.29, mean_slope=4.3304e-04,
                           mean_intercept=-0.01631567,
                           std_a=0.0047, std_b=0.6409, std_c=0.4999,
                           gev_c=0.0, gev_loc=0.0, gev_scale=1.0,
                           radius=2, fpSize=2048, useFeatures=False):
    """Process a batch of targets in a single Celery task."""
    # Retrieve shared input from Redis (stored once, not per-task)
    raw = redis_cache.get(smiles_key)
    if raw is None:
        raise ValueError(f"Input data not found in Redis for key: {smiles_key}")
    smiles_test = pickle.loads(raw)
    # Compute fingerprints once per batch (not once per target)
    data_test, mol_test = get_smiles_property(smiles_test)

    results = []
    for target in targets:
        try:
            known = get_cached_target(target)
            similarity = known_test_tc(known, data_test, fpSize)
            z_score, p_value, log_sf, raw_score, size = sea_score(
                similarity, tc_cutoff, mean_slope, mean_intercept,
                std_a, std_b, std_c,
                gev_c=gev_c, gev_loc=gev_loc, gev_scale=gev_scale)
            target_name = known["target_name"].iloc[0]
            results.append((target_name, z_score, p_value))
        except Exception as e:
            print(f"Error processing target {target}: {e}")
            target_name = os.path.basename(target).split(".")[0]
            results.append((target_name, None, None))

        # Update progress atomically via Redis
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
            (name, z, p, chembl_path)
            for (name, z, p), chembl_path in zip(group_results, list_chembl)
            if z is not None and p is not None
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

        target_name_list, z_score_summary, p_value_summary, valid_chembl = zip(*valid)

        list_chembl_names = []
        for item in valid_chembl:
            target_name_with_species = "/".join(item.split('.')[0].split("/")[-2:])
            list_chembl_names.append(target_name_with_species)

        sea_result = pd.DataFrame()
        sea_result["Target_Chembl_ID"] = [x.split('/')[1] for x in list_chembl_names]
        sea_result["Target_Species"] = [x.split('/')[0] for x in list_chembl_names]
        sea_result["Z_score"] = np.round(z_score_summary, 2)
        sea_result["p(P_value)"] = np.round(-np.log10(np.array(p_value_summary)), 4)
        sea_result["Target_Name"] = target_name_list

        # Annotate with Uniprot accession and sequence
        lookup_file = os.path.join(data_folder, "uniprot_chembl_id.tsv")
        if os.path.exists(lookup_file):
            lookup = pd.read_csv(lookup_file, sep="\t", index_col=0)
            sea_result = sea_result.merge(
                lookup[["target_chembl_id", "uniprot_accession", "sequence"]],
                left_on="Target_Chembl_ID", right_on="target_chembl_id", how="left",
            )
            sea_result = sea_result.drop(columns=["target_chembl_id"])
            sea_result = sea_result.rename(columns={
                "uniprot_accession": "Uniprot_ID",
                "sequence": "Sequence",
            })
        else:
            _write_log_(phase1_log, f"Warning: Uniprot lookup file not found at {lookup_file}")

        sea_result = sea_result.sort_values(by="p(P_value)", ignore_index=True)
        n_before_filter = len(sea_result)
        sea_result = sea_result[sea_result["p(P_value)"] > 3]
        _write_log_(phase1_log, f"Significant hits (p(P_value) > 3): {len(sea_result)} of {n_before_filter}")
        sea_result.to_csv(f"{save_folder}/sea_result.tsv", sep="\t", index=False)

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

    # --- Phase 2: Similarity scores for each hit target ---
    try:
        n_hit_targets = sea_result.shape[0]
        _write_log_(phase2_log, f"Phase 2 started: computing similarity scores for {n_hit_targets} hit targets")
        heatmap_data = []
        data_test, mol_test = get_smiles_property(smiles_test)
        # Pre-build lookup dict once (used in every target iteration)
        name_to_smiles = dict(zip(data_test["Name"], data_test["SMILES"]))
        test_names = data_test["Name"].tolist()
        n_test = len(test_names)
        _write_log_(phase2_log, f"Query compounds: {n_test} ({', '.join(test_names)})")

        phase2_errors = 0
        for idx in range(sea_result.shape[0]):
            try:
                target_chembl_id = sea_result["Target_Chembl_ID"].iloc[idx]
                species = sea_result["Target_Species"].iloc[idx]
                known = get_cached_target(f"{data_folder}/{species}/{target_chembl_id}.tsv")
                similarity = known_test_tc(known, data_test, fpSize=2048)
                sim_rounded = np.round(similarity, 4)
                known_ids = known["molecule_chembl_id"].tolist()
                known_smiles = dict(zip(known["molecule_chembl_id"], known["canonical_smiles"]))
                target_cid = known["target_chembl_id"].iloc[0]
                target_nm = known["target_name"].iloc[0]

                # Best similarity per test compound (max across known axis)
                best_idx = np.argmax(sim_rounded, axis=0)
                best_tc = np.max(sim_rounded, axis=0)
                best_known_ids = [known_ids[i] for i in best_idx]

                best_similarity = pd.DataFrame({
                    "Name": test_names,
                    "SMILES": data_test["SMILES"].tolist(),
                    "Target_Chembl_Id": target_cid,
                    "Target_Name": target_nm,
                    "Most_similar_known": best_known_ids,
                    "Tanimoto_Coefficient": best_tc,
                    "Known_Hit_SMILES": [known_smiles[kid] for kid in best_known_ids],
                })
                best_similarity = best_similarity.sort_values(by=["Tanimoto_Coefficient"], ascending=False, ignore_index=True)
                best_similarity = best_similarity[best_similarity["Tanimoto_Coefficient"] >= float(os.getenv("TC_CUTOFF", "0.28"))]
                best_similarity.to_csv(f"{save_folder}/best_similar_{target_cid}.tsv", sep="\t", index=False)

                # Top 10 similarity matrix — use argpartition instead of full sort
                summary_tc = []
                summary_ref = []
                summary_known = []
                summary_ref_smiles = []
                summary_known_smiles = []
                k = min(10, similarity.shape[0])
                for idxC in range(n_test):
                    col_vals = sim_rounded[:, idxC]
                    if k < len(col_vals):
                        top_k_idx = np.argpartition(col_vals, -k)[-k:]
                    else:
                        top_k_idx = np.arange(len(col_vals))
                    top_k_idx = top_k_idx[np.argsort(col_vals[top_k_idx])[::-1]]
                    top_tc = col_vals[top_k_idx].tolist()
                    top_known_ids = [known_ids[i] for i in top_k_idx]

                    summary_tc += top_tc
                    summary_ref += [test_names[idxC]] * len(top_k_idx)
                    summary_known += top_known_ids
                    summary_ref_smiles += [name_to_smiles[test_names[idxC]]] * len(top_k_idx)
                    summary_known_smiles += [known_smiles[kid] for kid in top_known_ids]
                similarity_matrix = pd.DataFrame({
                    "Name": summary_ref,
                    "Tanimoto_Score": summary_tc,
                    "SMILES": summary_ref_smiles,
                    "Known_Hit_ID": summary_known,
                    "Known_Hit_SMILES": summary_known_smiles,
                })
                similarity_matrix = similarity_matrix.sort_values(by=["Name", "Tanimoto_Score"], ascending=[True, False], ignore_index=True)
                similarity_matrix.to_csv(f"{save_folder}/top10_{target_cid}.tsv", sep="\t", index=False)
                similarity_matrix['Target_ChEMBL_ID'] = target_cid
                similarity_matrix['Target_Name'] = target_nm
                heatmap_data += similarity_matrix.to_dict(orient="records")
                _write_log_(phase2_log, f"[{idx+1}/{n_hit_targets}] {target_cid} ({target_nm}): known={len(known_ids)}, max_tc={float(best_tc.max()):.4f}, high_sim_hits={len(best_similarity)}")
            except Exception as e:
                phase2_errors += 1
                _write_log_(phase2_log, f"[{idx+1}/{n_hit_targets}] Target error ({target_chembl_id}): {e}\n{traceback.format_exc()}")
                print(f"Error calculating similarity for target {target_chembl_id}: {e}")
                continue

        _write_log_(phase2_log, f"Phase 2 completed: {n_hit_targets - phase2_errors}/{n_hit_targets} targets processed, {phase2_errors} errors")
        save_progress(task_id, {
            'progress': 100, 'status': 'RUNNING', 'error': None,
            'sea_calculated': True, 'best_similarity_calculated': True,
            'heatmap_calculated': False, 'failed': False,
        })
    except Exception as e:
        _write_log_(phase2_log, f"Phase 2 FAILED: {e}\n{traceback.format_exc()}")
        print(f"Error in similarity calculation: {e}")
        save_progress(task_id, {
            'progress': 100, 'status': 'ERROR', 'error': str(e),
            'sea_calculated': True, 'best_similarity_calculated': False,
            'heatmap_calculated': False, 'failed': True,
        })
        release_job_slot(task_id)
        raise

    # --- Phase 3: Heatmap data ---
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
                tkey = f"{hit["Target_ChEMBL_ID"]}:{hit["Target_Name"]}"
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
        save_progress(task_id, {
            'progress': 100, 'status': 'FAILED',
            'error': f"Error generating heatmap: {e}",
            'sea_calculated': True, 'best_similarity_calculated': True,
            'heatmap_calculated': False, 'failed': True,
        })
        release_job_slot(task_id)
        raise

    _write_log_(phase3_log, f"Phase 3 completed: {len(heatmap_result)} compounds written to .heatmap")

    # --- Phase 4: Generate Boltz-2 job folders ---
    phase4_log = f"{log_dir}/boltz.log"
    try:
        _write_log_(phase4_log, "Phase 4 started: generating Boltz-2 job folders")
        boltz_out = f"{job_dir}/{task_id}/boltz_jobs"
        test_file = f"{job_dir}/{task_id}/input.smi"
        result_dir = f"{job_dir}/{task_id}/sea_output"
        lookup_file = os.path.join(os.getenv("SEA_DATA_FOLDER"), "uniprot_chembl_id.tsv")
        manifest = _generate_boltz_jobs(
            result_dir=result_dir,
            testFile=test_file,
            out_dir=boltz_out,
            tc_min=0.0,
            lookup_file=lookup_file,
            predict_affinity=True,
            log_file=phase4_log,
        )
        _write_log_(phase4_log, f"Phase 4 completed: {manifest.shape[0]} Boltz jobs written to {boltz_out}")
    except RuntimeError as e:
        _write_log_(phase4_log, f"Phase 4 skipped: {e}")
    except Exception as e:
        _write_log_(phase4_log, f"Phase 4 FAILED: {e}\n{traceback.format_exc()}")

    save_progress(task_id, {
        'progress': 100, 'status': 'SUCCESS', 'error': None,
        'sea_calculated': True, 'best_similarity_calculated': True,
        'heatmap_calculated': True, 'failed': False,
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
    best_similarity = best_similarity[best_similarity["Tanimoto_Coefficient"] >= float(os.getenv("TC_CUTOFF", "0.28"))]
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
   
    save_folder = os.environ.get("SEA_JOB_DIR")
    # keep the last 30 jobs in the folder or remove the job folder what are older than 3 hours
    if len(os.listdir(save_folder)) < 30:
        return "No cleanup needed. There are currently {} job folders.".format(len(os.listdir(save_folder)))
    
    import shutil
    import time
    for dir in os.listdir(save_folder):
        dir_path = os.path.join(save_folder, dir)
        if os.path.isdir(dir_path):
            dir_time = os.path.getmtime(dir_path)
            if (time.time() - dir_time) > 3 * 3600 and len(os.listdir(save_folder)) > 30:  # 1 day 
                shutil.rmtree(dir_path)
                print(f"Removed old job folder: {dir_path}")
    return "Cleanup completed."

@celery.task(name="boltz.drain_queue")
def drain_boltz_queue():
    """Submit queued Boltz2 jobs to SLURM when running slots are available."""
    import subprocess, glob as _glob, json as _json

    # Distributed lock — only one drain runs at a time across all workers
    lock_key = "boltz_drain_lock"
    lock_ttl = 60  # seconds; longer than one drain cycle
    acquired = r.set(lock_key, "1", nx=True, ex=lock_ttl)
    if not acquired:
        return

    try:
        MAX_RUNNING = int(os.getenv("BOLTZ_MAX_RUNNING", 4))

        # Count how many Boltz jobs are currently in SLURM (pending or running)
        try:
            try:
                import pwd as _pwd
                username = _pwd.getpwuid(os.getuid()).pw_name
            except KeyError:
                username = str(os.getuid())
            sq = subprocess.run(
                ["squeue", "--noheader", "--format=%j", f"--user={username}"],
                capture_output=True, text=True, timeout=10,
            )
            if sq.returncode != 0:
                print(f"drain_boltz_queue: squeue error: {sq.stderr.strip()}")
                return
            running = sum(1 for line in sq.stdout.splitlines() if line.strip().startswith("sea_boltz2_"))
        except Exception as e:
            print(f"drain_boltz_queue: cannot count running jobs: {e}")
            return  # Fail safe: don't submit if we can't count

        slots_free = MAX_RUNNING - running
        if slots_free <= 0:
            return

        submitted = 0
        while submitted < slots_free:
            entry_raw = r.lpop("boltz_queue")
            if not entry_raw:
                break
            try:
                entry = _json.loads(entry_raw)
                job_key      = entry["job_key"]
                boltz_dir    = entry["boltz_dir"]
                slurm_script = entry["slurm_script"]

                result = subprocess.run(["sbatch", slurm_script], capture_output=True, text=True, timeout=30)
                if result.returncode != 0:
                    print(f"drain_boltz_queue: sbatch failed for {job_key}: {result.stderr.strip()}")
                    # Put the job back at the front of the queue so it isn't lost
                    r.lpush("boltz_queue", entry_raw)
                    break

                job_id = result.stdout.strip().split()[-1]
                submitted += 1

                jobs_file = f"{boltz_dir}/.slurm_jobs.json"
                slurm_jobs = {}
                if os.path.exists(jobs_file):
                    with open(jobs_file) as _f:
                        slurm_jobs = _json.load(_f)
                if job_key in slurm_jobs:
                    slurm_jobs[job_key]["slurm_job_id"] = job_id
                    slurm_jobs[job_key]["status"] = "pending"
                    slurm_jobs[job_key]["submitted_at"] = __import__("datetime").datetime.utcnow().isoformat()
                with open(jobs_file, "w") as _f:
                    _json.dump(slurm_jobs, _f)

                print(f"drain_boltz_queue: submitted {job_key} → SLURM job {job_id} ({submitted}/{slots_free} slots used)")
            except Exception as e:
                print(f"drain_boltz_queue: error processing entry: {e}")
                r.lpush("boltz_queue", entry_raw)
                break
    finally:
        r.delete(lock_key)


@celery.task(name="boltz.update_status")
def update_boltz_status():
    """Periodically scan all .slurm_jobs.json files and update job statuses via squeue + output files."""
    import subprocess, glob as _glob, json as _json

    lock_key = "boltz_status_lock"
    lock_ttl = 60
    acquired = r.set(lock_key, "1", nx=True, ex=lock_ttl)
    if not acquired:
        return

    try:
        job_dir_base = os.getenv("SEA_JOB_DIR", "")
        if not job_dir_base:
            return

        # Fetch all active SLURM jobs in one call
        try:
            import pwd as _pwd
            username = _pwd.getpwuid(os.getuid()).pw_name
        except (KeyError, ImportError):
            username = str(os.getuid())

        sq = subprocess.run(
            ["squeue", "--noheader", "--format=%i %T", f"--user={username}"],
            capture_output=True, text=True, timeout=10,
        )
        squeue_ok = sq.returncode == 0
        active_jobs = {}  # job_id -> SLURM state string
        if squeue_ok:
            for line in sq.stdout.strip().splitlines():
                parts = line.strip().split()
                if len(parts) >= 2:
                    active_jobs[parts[0]] = parts[1]

        _STATE_MAP = {
            "PENDING": "pending", "CONFIGURING": "pending",
            "RUNNING": "running", "COMPLETING": "running",
            "COMPLETED": "done",
            "FAILED": "failed", "CANCELLED": "failed", "TIMEOUT": "failed",
            "OUT_OF_MEMORY": "failed",
        }
        _TERMINAL = {"done", "failed"}

        for jobs_file in _glob.glob(f"{job_dir_base}/*/boltz_jobs/.slurm_jobs.json"):
            boltz_dir = os.path.dirname(jobs_file)
            try:
                with open(jobs_file) as _f:
                    slurm_jobs = _json.load(_f)
            except Exception:
                continue

            dirty = False
            for job_key, info in slurm_jobs.items():
                if info.get("status") in _TERMINAL:
                    continue
                job_id = info.get("slurm_job_id")
                if not job_id or info.get("status") == "queued":
                    continue

                str_job_id = str(job_id)

                if squeue_ok and str_job_id in active_jobs:
                    new_status = _STATE_MAP.get(active_jobs[str_job_id], "unknown")
                    # Clear stale missing_since if job reappears (shouldn't happen, but be safe)
                    if "missing_since" in slurm_jobs[job_key]:
                        del slurm_jobs[job_key]["missing_since"]
                        dirty = True
                elif squeue_ok and str_job_id not in active_jobs:
                    # Job has left the queue — check output files first
                    pred_base = os.path.join(boltz_dir, job_key, "boltz_out",
                                             f"boltz_results_{job_key}", "predictions", job_key)
                    if os.path.isdir(pred_base) and _glob.glob(os.path.join(pred_base, "*.cif")):
                        new_status = "done"
                    else:
                        # No output yet — use a grace period before marking failed,
                        # because the filesystem may still be flushing output files.
                        from datetime import datetime as _dt
                        missing_since = info.get("missing_since")
                        if missing_since is None:
                            slurm_jobs[job_key]["missing_since"] = _dt.utcnow().isoformat()
                            dirty = True
                            continue
                        elapsed = (_dt.utcnow() - _dt.fromisoformat(missing_since)).total_seconds()
                        if elapsed < 900:  # 15-minute grace period
                            continue
                        new_status = "failed"
                else:
                    # squeue unavailable — skip to avoid false negatives
                    continue

                if new_status in _TERMINAL and info.get("status") != new_status:
                    slurm_jobs[job_key]["status"] = new_status
                    slurm_jobs[job_key].pop("missing_since", None)
                    dirty = True
                    print(f"update_boltz_status: {job_key} → {new_status}")

            if dirty:
                with open(jobs_file, "w") as _f:
                    _json.dump(slurm_jobs, _f)
    finally:
        r.delete(lock_key)


celery.conf.beat_schedule = {
    'daily-cleanup-task': {
        'task': 'main.cleanup_task',
        'schedule': crontab(minute=10, hour='*'),  # Run every hour
    },
    'drain-boltz-queue': {
        'task': 'boltz.drain_queue',
        'schedule': 30.0,  # every 30 seconds
    },
    'update-boltz-status': {
        'task': 'boltz.update_status',
        'schedule': 60.0,  # every 60 seconds
    },
}

celery.conf.timezone = 'UTC'













