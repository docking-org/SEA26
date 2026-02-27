from celery import Celery, current_task, shared_task, chord, current_app, group
from celery.result import GroupResult
from celery.schedules import crontab
import pandas as pd
import numpy as np
from .helper import get_smiles_property, _mkdir_, remove_salt, known_test_tc, sea_score
from dotenv import load_dotenv
import os

load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))


celery = Celery(
    "sea-worker",
    broker="redis://localhost:6380/0",
    backend="redis://localhost:6380/1",
)
celery.autodiscover_tasks()

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
    data_test, mol_test = get_smiles_property(input)
    known = pd.read_csv(f"{target}", sep = "\t", index_col = 0)
    similarity = known_test_tc(known, data_test, fpSize)
    result = pd.DataFrame(np.round(similarity, 4), index = known["molecule_chembl_id"].tolist(), 
                          columns = data_test["Name"].tolist())

    z_score, p_value = sea_score(result, tc_cutoff, mean_para, std_coef, std_exp)
    target_name = known["target_name"].tolist()[0]

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
    if len(os.listdir(save_folder)) < 3:
        return "No cleanup needed."
    
    import shutil
    for dir in os.listdir(save_folder):
        dir_path = os.path.join(save_folder, dir)
        if os.path.isdir(dir_path):
            dir_time = os.path.getmtime(dir_path)
            import time
            if (time.time() - dir_time) > 86400 and len(os.listdir(save_folder) > 3):  # 1 day in seconds
                shutil.rmtree(dir_path)
                print(f"Removed old job folder: {dir_path}")
    return "Cleanup completed."

celery.conf.beat_schedule = {
    'daily-cleanup-task': {
        'task': 'main.cleanup_task',
        'schedule': crontab(hour=0, minute=0),  # Run daily at midnight
    },
}

celery.conf.timezone = 'UTC'  
    



    








    