import os
import shutil
import json
import uuid
import requests
import pandas as pd
from typing import Union

from fastapi import FastAPI, APIRouter
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from celery.utils.log import get_task_logger
from dotenv import load_dotenv

# import sea analysis functions
import numpy as np
from .celery_worker import celery
from celery import group
from .celery_worker import _mkdir_, remove_salt, sea_target_score
from .helper import get_smiles_property, known_test_tc, tsv2json

env_path = os.path.join(os.path.dirname(__file__), "../.env")
load_dotenv(env_path)

# app = FastAPI(docs_url="/api/py/docs", openapi_prefix="/api", openapi_url="/api/py/openapi.json")
router = APIRouter(prefix="/api")
app = FastAPI()


# Allow requests from your frontend origin
origins = [
    "http://localhost:3001",
    "http://127.0.0.1:3001",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,          # You can set ["*"] for all (not recommended in prod)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


task_logger = get_task_logger(__name__)



@router.get("" or "/")
def read_root() -> dict[str, Union[str, int]]:
  
    return {"msg": "Welcome to SEA", "version": 1}

@router.get("/items/{item_id}")
def read_item(item_id: int, q: Union[str, None] = None) -> dict[str, Union[int, str, None]]:
    return {"item_id": item_id, "q2": q}

@router.post("/submit")
async def submit_task(request: Request) -> dict[str, str]:
    input = await request.json()
    print("Received input:", input)
    # set up celery task
    parent_task_id = str(uuid.uuid4())
    job_dir = f"{os.getenv('SEA_JOB_DIR')}/{parent_task_id}"
    _mkdir_(job_dir)
    os.chdir(job_dir)
    input_file = f"input.smi"
    with open(input_file, "w") as f:
        f.write(input["query"])

    list_chembl = []
    for file in os.listdir(os.getenv("SEA_DATA_FOLDER")):
        full_file_name = os.path.join(os.getenv("SEA_DATA_FOLDER"), file)
        if os.path.isfile(full_file_name):
            list_chembl.append(full_file_name)
    smiles_test = [f"{remove_salt(x.strip().split()[0])} {x.strip().split()[1]}" for x in input["query"].splitlines() if x.strip()]
    save_folder = f"{job_dir}/sea_output"
    _mkdir_(save_folder)  
    # create group of tasks for each target
    task_group = group(
        sea_target_score.s(
            input=smiles_test,
            target=target,
        )     for target in list_chembl 
    )
    group_task = task_group.apply_async() # submit group task
    group_task.save()
    # rename the job directory to include celery task id
    new_job_dir = f"{os.getenv('SEA_JOB_DIR')}/{group_task.id}"
    os.rename(job_dir, new_job_dir)
    return {"task_id": group_task.id}

@router.get("/result/{sea_score_task_id}")
def get_task_status(sea_score_task_id: str):

    sea_result_file = f"{os.getenv('SEA_JOB_DIR')}/{sea_score_task_id}/sea_output/sea_result.tsv"
    if os.path.exists(sea_result_file):
        job_info = {
            "stage" : 2,
            "status": "SUCCESS",
            "msg" : "SEA analysis completed.",
            "progress": 100,
        }
        table_result = tsv2json(sea_result_file)
        job_info["result"] = table_result
        # looad job info
        input_f = f"{os.getenv('SEA_JOB_DIR')}/{sea_score_task_id}/input.smi"
        smiles_test = [f"{remove_salt(x.strip().split()[0])} {x.strip().split()[1]}" for x in open(input_f).read().splitlines() if x.strip()]
        job_info['input'] = smiles_test       
        return job_info
    
    # Retrieve the task using its ID
    sea_analysis_group_task = celery.GroupResult.restore(sea_score_task_id)
    print("Restored task:", sea_analysis_group_task)
    # looad job info
    input_f = f"{os.getenv('SEA_JOB_DIR')}/{sea_score_task_id}/input.smi"
    smiles_test = [f"{remove_salt(x.strip().split()[0])} {x.strip().split()[1]}" for x in open(input_f).read().splitlines() if x.strip()]
    if not sea_analysis_group_task:
        return {"state": "UNKNOWN", "status": "No task found with the given ID."}
    completed = sea_analysis_group_task.completed_count()
    total = len(sea_analysis_group_task.results)
    progress = int((completed / total) * 100)

    if progress > 0 and progress < 100:
        job_info = {
                "stage" : 1,
                "status": "PROGRESS",
                "msg" : f"SEA analysis in progress: {completed} out of {total} targets completed.",
                "progress": progress,
            }
    elif progress == 100:
        job_info = {
            "stage" : 1,
            "status": "SUCCESS",
            "msg" : f"SEA analysis completed for all {total} targets.",
            "progress": progress,
        }
        # collect results from all subtasks
        job_results = []
        for result in sea_analysis_group_task.results:
            job_results.append(result.get())
        target_name_list, z_score_summary, p_value_summary = zip(*job_results)
        save_folder = f"{os.getenv('SEA_JOB_DIR')}/{sea_score_task_id}/sea_output"
        if not os.path.exists(save_folder):
            _mkdir_(save_folder)
        list_chembl = []
        for file in os.listdir(os.getenv("SEA_DATA_FOLDER")):
            full_file_name = os.path.join(os.getenv("SEA_DATA_FOLDER"), file)
            if os.path.isfile(full_file_name):
                list_chembl.append(full_file_name)
        sea_result = pd.DataFrame()
        sea_result["Target_Chembl_ID"] = [x.split("/")[-1].split(".")[0] for x in list_chembl]
        sea_result["Z_score"] = np.round(z_score_summary, 2)
        sea_result["p(P_value)"] = np.round(-np.log10(np.array(p_value_summary)), 4)
        sea_result["Target_Name"] = target_name_list
        sea_result = sea_result.sort_values(by="p(P_value)", ignore_index=True)
        sea_result = sea_result[sea_result["p(P_value)"] > 3]
        sea_result.to_csv(f"{save_folder}/sea_result.tsv", sep="\t", index=False)

        table_result = sea_result.to_dict(orient="records")
        job_info["result"] = table_result
        job_info['stage'] = 2
        
        # calculate similarity scores for top targets synchronously for now
        data_test, mol_test = get_smiles_property(smiles_test)
        for idx in np.arange(sea_result.shape[0]):
            target_chembl_id = sea_result["Target_Chembl_ID"].tolist()[idx]
            known = pd.read_csv(f"{os.getenv("SEA_DATA_FOLDER")}/{target_chembl_id}.tsv", sep = "\t", index_col = 0)
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
            
        job_info["msg"] = "SEA analysis and similarity calculations completed."
        job_info["progress"] = 100
        job_info['input'] = smiles_test       

    return job_info

@router.get("")

# download result file
@router.get("/download/{task_id}")
def download_result_file(task_id: str):
    ## zip the result folder
    job_result_dir = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/result_input"
    zip_file = f"{job_result_dir}.zip"
    shutil.make_archive(job_result_dir, 'zip', job_result_dir)
    print("Created zip file:", zip_file)
    return FileResponse(zip_file, media_type='application/zip', filename=f"sea_result_{task_id}.zip")

@router.get("/best_similarity/{task_id}")
def get_best_similarity_per_target(request: Request, task_id: str):
    print("Fetching best similarity for task:", task_id)
    target_chembl_id = request.query_params.get("target_chembl_id")
    print("Target ChEMBL ID:", target_chembl_id)
    print("Fetching best similarity for target:", target_chembl_id)
    best_similarity_file = f"{os.getenv('SEA_JOB_DIR')}/{task_id}/sea_output/top10_{target_chembl_id}.tsv"
    if not os.path.exists(best_similarity_file):
        return {"error": "Best similarity file not found for the given target."}
    best_similarity_json = tsv2json(best_similarity_file) 
    return {"task_id": task_id, "target_chembl_id": target_chembl_id, "best_similarity": best_similarity_json}



## TODO: the structure is very messy, need to clean up after
# Put all API routes under /api
app.include_router(router)

# frontend import
frontend_path = os.path.join(os.path.dirname(__file__), "../../frontend/out")
app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")

@app.get("/{full_path:path}")
async def serve_frontend(full_path: str) -> FileResponse:
    index_path = os.path.join(frontend_path, "index.html")
    return FileResponse(index_path)