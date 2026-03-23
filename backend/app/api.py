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
from dotenv import load_dotenv

# import sea analysis functions
import numpy as np
from .celery_worker import celery
from celery import group
from .celery_worker import _mkdir_, remove_salt, sea_target_score, warm_target_cache
from .helper import get_smiles_property, known_test_tc, tsv2json, init_progress_file, save_progress, get_progress

env_path = os.path.join(os.path.dirname(__file__), "../../.env")
load_dotenv(env_path)

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
async def submit_task(request: Request) -> dict[str, str]:
    input_data = await request.json()
    
    # Parse SMILES first (fast)
    smiles_test = [
        f"{remove_salt(x.strip().split()[0])} {x.strip().split()[1]}" 
        for x in input_data["query"].splitlines() 
        if x.strip()
    ]
    
    # Get cached target files 
    list_chembl = _TARGET_FILES_CACHE

    # Create and submit task group FIRST to get the ID
    task_group = group(
        sea_target_score.s(input=smiles_test, target=target)
        for target in list_chembl
    )
    group_task = task_group.apply_async()
    group_task.save()
    
    # Now create directory with the actual task ID (no rename needed)
    task_id = group_task.id
    job_dir = os.path.join(os.getenv('SEA_JOB_DIR'), task_id)
    os.makedirs(job_dir, exist_ok=True)
    
    # Write input file
    input_file = os.path.join(job_dir, "input.smi")
    with open(input_file, "w") as f:
        f.write(input_data["query"])
    # Create output directory
    save_folder = os.path.join(job_dir, "sea_output")
    os.makedirs(save_folder, exist_ok=True)
    init_progress_file(task_id)
    
    return {"task_id": task_id}


@router.get("/result")
def get_task_status(request: Request):
    task_id = request.query_params.get("taskId")
    sea_result_file = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/sea_output/sea_result.tsv"
    with open(f"{os.getenv('SEA_JOB_DIR')}/{task_id}/.progress") as pf:
        job_info = json.load(pf)

    if os.path.exists(sea_result_file):
        table_result = tsv2json(sea_result_file)
        job_info["result"] = table_result
        # load job info
        input_f = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/input.smi"
        smiles_test = [f"{remove_salt(x.strip().split()[0])} {x.strip().split()[1]}" for x in open(input_f).read().splitlines() if x.strip()]
        job_info['input'] = smiles_test       
        return job_info
    
    # Retrieve the task using its ID
    sea_analysis_group_task = celery.GroupResult.restore(task_id)
    print("Restored task:", sea_analysis_group_task)
    
    # Load job info
    input_f = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/input.smi"
    smiles_test = [f"{remove_salt(x.strip().split()[0])} {x.strip().split()[1]}" for x in open(input_f).read().splitlines() if x.strip()]
    
    if not sea_analysis_group_task:
        return {"state": "UNKNOWN", "status": "No task found with the given ID."}
    
    completed = sea_analysis_group_task.completed_count()
    total = len(sea_analysis_group_task.results)
    progress = int((completed / total) * 100)
    print(progress)


    print(f"Task {task_id} progress: {progress}% ({completed}/{total} completed)")

    job_info = get_progress(task_id)
    save_progress(task_id=task_id,
        progress_data ={
                    'progress': progress,
                    'status': 'RUNNING',
                    'error': None,
                    'sea_calculated': False,
                    'best_similarity_calculated': False,
                    'heatmap_calculated': False,
                    'failed': False,
                
                        }
                )
     
    if progress == 100:
    
        # collect results from all subtasks
        try:
            job_results = []
            for result in sea_analysis_group_task.results:
                job_results.append(result.get())
            target_name_list, z_score_summary, p_value_summary = zip(*job_results)
            save_folder = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/sea_output"
            if not os.path.exists(save_folder):
                _mkdir_(save_folder)
            list_chembl = []
            for item in _TARGET_FILES_CACHE:
                target_name_with_species = "/".join(item.split('.')[0].split("/")[-2:]) 
                list_chembl.append(target_name_with_species)
            sea_result = pd.DataFrame()
            sea_result["Target_Chembl_ID"] = [x.split('/')[1] for x in list_chembl]
            sea_result["Target_Species"] = [x.split('/')[0] for x in list_chembl]
            sea_result["Z_score"] = np.round(z_score_summary, 2)
            sea_result["p(P_value)"] = np.round(-np.log10(np.array(p_value_summary)), 4)
            sea_result["Target_Name"] = target_name_list
            sea_result = sea_result.sort_values(by="p(P_value)", ignore_index=True)
            sea_result = sea_result[sea_result["p(P_value)"] > 3] 
            sea_result.to_csv(f"{save_folder}/sea_result.tsv", sep="\t", index=False)

            table_result = sea_result.to_dict(orient="records")
            job_info["result"] = table_result
            save_progress(task_id=task_id,
                    progress_data = {
                        'progress': progress,
                        'status': 'RUNNING',
                        'error': None,
                        'sea_calculated': True,
                        'best_similarity_calculated': True,
                        'heatmap_calculated': False,
                        'failed': False
                        }
                    ) 
        except Exception as e:
            print(f"Error aggregating SEA results: {e}")
            job_info["msg"] = "Error occurred while processing SEA results."
            job_info["status"] = "ERROR"
            save_progress(
                progress_data = {
                    'progress': progress,
                    'status': 'ERROR',
                    'error': str(e),
                    'sea_calculated': True,
                    'best_similarity_calculated': False,
                    'heatmap_calculated': False,
                    'failed': True
                }
            )
        
        # calculate similarity scores for top targets synchronously for now
        heatmap_data = []
        data_test, mol_test = get_smiles_property(smiles_test)
        for idx in np.arange(sea_result.shape[0]):
            try:
            
                target_chembl_id = sea_result["Target_Chembl_ID"].tolist()[idx]
                species = sea_result["Target_Species"].tolist()[idx]
                
                known = pd.read_csv(f"{os.getenv("SEA_DATA_FOLDER")}/{species}/{target_chembl_id}.tsv", sep = "\t", index_col = 0)
                similarity = known_test_tc(known, data_test, fpSize=2048)
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
                similarity_matrix['Target_ChEMBL_ID'] = known["target_chembl_id"].tolist()[0]

                heatmap_data += similarity_matrix.to_dict(orient="records")
            except Exception as e:
                print(f"Error calculating similarity for target {target_chembl_id}: {e}")
                save_progress(
                    progress_data = {
                        'progress': progress,
                        'status': 'ERROR',
                        'error': "Error calculating similarity for target {}: {}".format(target_chembl_id, str(e)),
                        'sea_calculated': True,
                        'best_similarity_calculated': False,
                        'heatmap_calculated': False,
                        'failed': True
                    }
                )
                continue
           


            
        job_info["msg"] = "SEA analysis and similarity calculations completed."
        job_info["progress"] = 100
        job_info['input'] = smiles_test     

        # process heatmap data
        # Group by Name and SMILES
        try:
            group_fields = {"Name", "SMILES"}
            grouped_dict = {}

            for item in heatmap_data:
                key = (item["Name"], item["SMILES"])
                if key not in grouped_dict:
                    grouped_dict[key] = []   
                hit = {k: item[k] for k in item if k not in group_fields}
                grouped_dict[key].append(hit)
            result = []
            for (name, smiles), hits in grouped_dict.items():
                grouped_target_hits = {}
                for hit in hits:
                    key = hit["Target_ChEMBL_ID"]
                    if key not in grouped_target_hits:
                        grouped_target_hits[key] = []
                    grouped_target_hits[key].append({
                        "Known_Hit_ID" : hit["Known_Hit_ID"],
                        "Known_Hit_SMILES" : hit["Known_Hit_SMILES"],
                        "Tanimoto_Score" : hit["Tanimoto_Score"]
                    })
                result.append({
                    "Name": name,
                    "SMILES": smiles,
                    "hits": grouped_target_hits
                })
            heatmap_file = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/.heatmap"
            with open(heatmap_file, "w") as hm:
                json.dump(result, hm, indent=4)
        except Exception as e:
                save_progress(
                    progress_data = {
                        'progress': progress,
                        'status': 'ERROR',
                        'error': "Error calculating heatmap for target {}: {}".format(target_chembl_id, str(e)),
                        'sea_calculated': True,
                        'best_similarity_calculated': True,
                        'heatmap_calculated': False,
                        'failed': True
                    }
                )
        save_progress(task_id=task_id,
                progress_data = {
                    'progress': progress,
                    'status': 'SUCCESS',
                    'error': None,
                    'sea_calculated': True,
                    'best_similarity_calculated': True,
                    'heatmap_calculated': True,
                    'failed': False
                }
            )

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