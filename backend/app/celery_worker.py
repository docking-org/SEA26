from celery import Celery, current_task, shared_task, chord, current_app, group
from celery.result import GroupResult
from celery.schedules import crontab
import pandas as pd
import numpy as np
import redis
import pickle
from .helper import get_smiles_property, _mkdir_, remove_salt, known_test_tc, sea_score
from dotenv import load_dotenv
import os

load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))


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
    import glob

    data_folder = os.getenv("SEA_DATA_FOLDER")
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

@celery.task(name="sea.collect_sea_results")
def collect_sea_results(async_result):
    if async_result.ready():
        print("Collecting SEA results...")
        results = async_result.get()
        return results
    else:
        print("SEA tasks are not yet completed.")
        return None
    
@shared_task( name="sea.sea_target_score")
def sea_target_score(input, target,
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

        print(f"Completed SEA score for target: {target_name}, Z-score: {z_score:.2f}, p-value: {p_value:.4e}")
    except Exception as e:
        print(f"Error processing target {target}: {e}")
        target_name = os.path.basename(target).split(".")[0]
        z_score, p_value = None, None
    return target_name, z_score, p_value



@celery.task(name="sea.aggregate_sea_results")
def aggregate_sea_results(results, input, list_chembl, save_folder, parent_task_id=None):
    target_name_list, z_score_summary, p_value_summary = zip(*results)
    sea_result = pd.DataFrame()
    sea_result["Target_Chembl_ID"] = [x.split("/")[-1].split(".")[0] for x in list_chembl]
    sea_result["Z_score"] = np.round(z_score_summary, 2)
    sea_result["p(P_value)"] = np.round(-np.log10(np.array(p_value_summary)), 4)
    sea_result["Target_Name"] = target_name_list
    sea_result = sea_result.sort_values(by="p(P_value)", ignore_index=True)
    sea_result = sea_result[sea_result["p(P_value)"] > 3]
    sea_result.to_csv(f"{save_folder}/sea_result.tsv", sep="\t", index=False)
    print("SEA analysis completed.")
    return sea_result.to_dict(orient="records")




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
    best_similarity = best_similarity[best_similarity["Tanimoto_Coefficient"] >= 0.7]
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
    # keep the last 30 jobs in the folder or remove the job folder what are older than 1 day
    if len(os.listdir(save_folder)) < 30:
        return "No cleanup needed."
    
    import shutil
    import time
    for dir in os.listdir(save_folder):
        dir_path = os.path.join(save_folder, dir)
        if os.path.isdir(dir_path):
            dir_time = os.path.getmtime(dir_path)
            if (time.time() - dir_time) > 86400 and len(os.listdir(save_folder)) > 30:  # 1 day 
                shutil.rmtree(dir_path)
                print(f"Removed old job folder: {dir_path}")
    return "Cleanup completed."

celery.conf.beat_schedule = {
    'daily-cleanup-task': {
        'task': 'main.cleanup_task',
        'schedule': crontab(minute=10, hour='*'),  # Run every hour
    },
}

celery.conf.timezone = 'UTC'













