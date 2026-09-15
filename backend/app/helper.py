## Import module
from rdkit import Chem
import json
import os
import sys
import time
import multiprocessing as mp
import numpy as np
import pandas as pd

np.set_printoptions(suppress=True)

from .scripts.run_sea_genextreme import (
	_list_, _rm_, _mkdir_,
	get_smiles_property, remove_salt,
	get_morgan, get_morgan_with_feature, collect_fingerprint,
	calc_tanimoto_sets, calc_tanimoto_ref, max_tanimoto,
	known_test_tc,
	group_data, fit_gev_from_freq, load_gev_params,
	add_uniprot_info, taylor_gumbel_logsf, sea_score,
	sea_analysis,
)


################################################################
## Similarity Ensemble Approach (SEA)
## This is the script used for running SEA analysis
## Compute summarized Tc between test set and known sets
## Plug in to the analysis results -- Z-test
##	Check original SEA paper (Keiser et al., Nat Biotechnol 2007)
##
## Updates relative to run_sea_precalc.py
##	tc_cutoff = 0.29
##		Smallest joint GoF error in zscore_dist.py
##		See Parameterization/result_binwidth_0.25/Extreme_pdf_fit.tsv
##	Mean fit  : E[score]   = mean_slope * size + mean_intercept
##		Linear regression on param_avg_tanimoto.tsv (row tc = 0.29)
##	Std fit   : Std[score] = std_a * size ** std_b + std_c
##		3-parameter power law on param_std_tanimoto.tsv (row tc = 0.29)
##	p-value   : scipy.stats.genextreme survival function
##		c, loc, scale fitted on the null-set z-score histogram at
##		Tc = 0.29 (winning EVD by rank-sum across chi^2_Pearson,
##		chi^2_Neyman, KL).  Replaces the gumbel_r Taylor series
##		used in the original SEA code.
################################################################


################################################################
## Helper function
################################################################


def validate_smiles(smiles):
	'''validate smiles string'''
	try:
		mol = Chem.MolFromSmiles(smiles)
		if mol is not None:
			return Chem.MolToSmiles(mol)
		else:
			return None
	except Exception as e:
		print(f"Error validating SMILES: {e}")
		return None

def validate_smiles_input(input_text):
	"""Validate all SMILES in multi-line input text.

	Returns a list of (line_number, line_text) tuples for invalid entries.
	Lines starting with ZINC IDs are skipped (looked up separately).
	For 3-column format (target_id smiles compound_id), validates column 2.
	"""
	invalid = []
	lines = [x.strip() for x in input_text.strip().splitlines() if x.strip()]
	for i, line in enumerate(lines):
		parts = line.split()
		# 3-column format: target_id smiles compound_id
		if len(parts) >= 3:
			smiles_str = parts[1]
		else:
			smiles_str = parts[0]
		# Skip ZINC IDs
		if smiles_str.upper().startswith("ZINC"):
			continue
		if validate_smiles(smiles_str) is None:
			invalid.append((i + 1, line))
	return invalid

def tsv2json(input_tsv):
	df = pd.read_csv(input_tsv, sep="\t")
	json_str = df.to_json(orient="records", double_precision=15)
	json_data = json.loads(json_str)
	return json_data

def calculate_sea_results(input, target,
		tc_cutoff = 0.29, mean_slope = 4.3304e-04, mean_intercept = -0.01631567,
		std_a = 0.0047, std_b = 0.6409, std_c = 0.4999,
		gev_c = 0.0, gev_loc = 0.0, gev_scale = 1.0,
		radius = 2, fpSize = 2048, useFeatures = False) :
	known = pd.read_csv("{}".format(target), sep = "\t", index_col = 0)
	similarity = known_test_tc(known, input, fpSize)
	similarity = np.round(similarity, 4)
	best_tc = float(np.max(similarity)) if similarity.size > 0 else 0.0
	z_score, p_value, log_sf, raw_score, size = sea_score(
		similarity, tc_cutoff, mean_slope, mean_intercept,
		std_a, std_b, std_c, gev_c, gev_loc, gev_scale)
	target_name = known["target_name"].tolist()[0]
	target_chembl_id = known["target_chembl_id"].tolist()[0]
	return target_name, target_chembl_id, z_score, p_value, log_sf, raw_score, size, best_tc

def calculate_best_similarity(input, target, saveFolder,
		tc_cutoff = 0.29, mean_slope = 4.3304e-04, mean_intercept = -0.01631567,
		std_a = 0.0047, std_b = 0.6409, std_c = 0.4999,
		gev_c = 0.0, gev_loc = 0.0, gev_scale = 1.0,
		radius = 2, fpSize = 2048, useFeatures = False) :
	known = pd.read_csv("{}".format(target), sep = "\t", index_col = 0)
	similarity = known_test_tc(known, input, fpSize)
	result = pd.DataFrame(np.round(similarity, 4), index = known["molecule_chembl_id"].tolist(),
		columns = input["Name"].tolist())
	result = result.T
	known.index = known["molecule_chembl_id"].tolist()
	best_similarity = pd.DataFrame()
	best_similarity["Name"] = list(result.index)
	best_similarity["SMILES"] = input["SMILES"].tolist()
	best_similarity["Target_Chembl_Id"] = [known["target_chembl_id"].tolist()[0]] * best_similarity.shape[0]
	best_similarity["Target_Name"] = [known["target_name"].tolist()[0]] * best_similarity.shape[0]
	best_similarity["Most_similar_known"] = list(result.idxmax(axis = "columns"))
	best_similarity["Tanimoto_Coefficient"] = list(result.max(axis = 1))
	best_similarity["Known_Hit_SMILES"] = [known.loc["{}".format(name), "canonical_smiles"] for name in result.idxmax(axis = "columns")]
	best_similarity = best_similarity.sort_values(by = ["Tanimoto_Coefficient"], ascending = False, ignore_index = True)
	best_similarity = best_similarity[best_similarity["Tanimoto_Coefficient"] >= 0.7]
	best_similarity.to_csv("{}/best_similar_{}_{}.tsv".format(saveFolder, "input", known["target_chembl_id"].tolist()[0]), sep = "\t", index = False)

	# Get top 10 similarity matrix
	summary_tc = []
	summary_ref = []
	summary_known = []
	summary_ref_smiles = []
	summary_known_smiles = []
	result = pd.DataFrame(np.round(similarity, 4), columns = input["Name"].tolist())
	ref_compound = list(result.columns)
	known_compound = list(result.index)
	result["Known"] = known["molecule_chembl_id"].tolist()
	for idxC in np.arange(result.shape[1] - 1) :
		top10 = result.sort_values(by = ["{}".format(ref_compound[idxC])], ascending = False, ignore_index = True).head(10)
		summary_tc += top10["{}".format(ref_compound[idxC])].tolist()
		summary_ref += [ref_compound[idxC]] * top10.shape[0]
		summary_known += top10["Known"].tolist()
		summary_ref_smiles += [input["SMILES"].to_numpy()[np.where(input["Name"].to_numpy() == ref_compound[idxC])[0]][0]] * top10.shape[0]
		summary_known_smiles += [known.loc["{}".format(name), "canonical_smiles"] for name in top10["Known"].tolist()]
	similarity_matrix = pd.DataFrame()
	similarity_matrix["Name"] = summary_ref
	similarity_matrix["Tanimoto_Score"] = summary_tc
	similarity_matrix["SMILES"] = summary_ref_smiles
	similarity_matrix["Known_Hit_ID"] = summary_known
	similarity_matrix["Known_Hit_SMILES"] = summary_known_smiles
	similarity_matrix = similarity_matrix.sort_values(by = ["Name", "Tanimoto_Score"], ascending = [True, False], ignore_index = True)
	similarity_matrix.to_csv("{}/top10_{}_{}.tsv".format(saveFolder, "input", known["target_chembl_id"].tolist()[0]), sep = "\t", index = False)
	return


def sea_analysis_with_multiprocessing(testFile, list_chembl,
		tc_cutoff = 0.29, mean_slope = 4.3304e-04, mean_intercept = -0.01631567,
		std_a = 0.0047, std_b = 0.6409, std_c = 0.4999,
		gev_c = 0.0, gev_loc = 0.0, gev_scale = 1.0,
		p_threshold = 0.01, high_tc_threshold = 0.95,
		lookup_file = "uniprot_chembl_id_uniprot_info.tsv",
		radius = 2, fpSize = 2048, useFeatures = False, task = None) :
	task.update_state(state='PROGRESS', meta={'status': 'Preparing test compounds...'}) if task else None
	with open(testFile, "r") as f:
		smiles_test = f.readlines()
	smiles_test = [f"{remove_salt(x.strip().split()[0])} {x.strip().split()[1]}" for x in smiles_test]
	data_test, mol_test = get_smiles_property(smiles_test, radius, fpSize, useFeatures)
	saveFolder = "result_{}".format(testFile.split(".")[0])
	_mkdir_(saveFolder)

	target_name       = []
	target_chembl_ids = []
	z_score_summary   = []
	p_value_summary   = []
	log_sf_summary    = []
	raw_score_summary = []
	size_summary      = []
	best_tc_summary   = []

	count = 0
	print("Starting SEA analysis loop...")
	start_time = time.time()
	# replace for loop with multiprocessing pool
	with mp.Pool(5) as pool:
		results = [pool.apply_async(calculate_sea_results,
			args=(data_test, target, tc_cutoff, mean_slope, mean_intercept,
				std_a, std_b, std_c, gev_c, gev_loc, gev_scale,
				radius, fpSize, useFeatures)) for target in list_chembl]
		for idx, res in enumerate(results):
			count += 1
			new_percent_done = int((count / len(list_chembl)) * 100)
			print(f"multiprocessing known set {idx + 1} of {len(list_chembl)} ({new_percent_done}%)")
			task.update_state(state='PROGRESS',
				meta={'status': f'multiprocessing known set {idx + 1} of {len(list_chembl)}',
					'progress': new_percent_done}) if task else None
			target_n, target_cid, z_s, p_v, l_sf, r_s, sz, b_tc = res.get()
			target_name.append(target_n)
			target_chembl_ids.append(target_cid)
			z_score_summary.append(z_s)
			p_value_summary.append(p_v)
			log_sf_summary.append(l_sf)
			raw_score_summary.append(r_s)
			size_summary.append(sz)
			best_tc_summary.append(b_tc)
	end_time = time.time()
	print(f"SEA analysis loop time: {end_time - start_time} seconds")

	# -log10(p) computed from logsf to keep precision in the deep tail.
	neg_log10_p = - np.array(log_sf_summary) / np.log(10)

	sea_results = pd.DataFrame()
	sea_results["Target_chembl_id"] = list_chembl
	sea_results["CHEMBL_ID"]        = target_chembl_ids
	sea_results["Size"]             = size_summary
	sea_results["Raw_score"]        = np.round(raw_score_summary, 4)
	sea_results["Best_Tc"]          = np.round(best_tc_summary, 4)
	sea_results["Z_score"]          = np.round(z_score_summary, 2)
	sea_results["p_value"]          = p_value_summary
	sea_results["p(P_value)"]       = np.round(neg_log10_p, 4)
	sea_results["Target_name"]      = target_name

	# Annotate with Uniprot accession and sequence via lookup.
	if os.path.exists(lookup_file) :
		lookup = pd.read_csv(lookup_file, sep = "\t", index_col = 0)
		sea_results = sea_results.merge(
			lookup[["target_chembl_id", "uniprot_accession", "sequence"]],
			left_on = "CHEMBL_ID", right_on = "target_chembl_id", how = "left",
		)
		sea_results = sea_results.drop(columns = ["target_chembl_id"])
		sea_results = sea_results.rename(columns = {
			"uniprot_accession" : "Uniprot_ID",
			"sequence"          : "Sequence",
		})

	sea_results = sea_results.sort_values(by = ["p(P_value)"], ascending = False, ignore_index = True)

	# Save unfiltered results
	sea_results.to_csv("{}/sea_result_all.tsv".format(saveFolder), sep = "\t", index = False)

	# Filter: keep targets that pass p-value threshold OR high Tc rescue
	sea_results = sea_results[
		(sea_results["p_value"] <= p_threshold) |
		(sea_results["Best_Tc"] >  high_tc_threshold)
	]

	print("Starting detailed results loop...")
	count = 0
	with mp.Pool(30) as pool:
		results = [pool.apply_async(calculate_best_similarity,
			args=(data_test, target, saveFolder, tc_cutoff, mean_slope, mean_intercept,
				std_a, std_b, std_c, gev_c, gev_loc, gev_scale,
				radius, fpSize, useFeatures)) for target in sea_results["Target_chembl_id"].tolist()]
		# wait for all to finish
		for idx, res in enumerate(results):
			count += 1
			new_percent_done = int((count / sea_results.shape[0]) * 100)
			print(f"multiprocessing detailed results {new_percent_done}% done")
			task.update_state(state='PROGRESS',
				meta={'status': f'multiprocessing detailed results {new_percent_done}% done',
					'progress': new_percent_done}) if task else None
			res.get()
	# Save filtered results
	sea_results["Target_chembl_id"] = [x.split("/")[-1].split(".")[0] for x in sea_results["Target_chembl_id"].tolist()]
	sea_results = sea_results.sort_values(by = ["p(P_value)"], ignore_index = True)
	sea_results.to_csv("{}/sea_result.tsv".format(saveFolder), sep = "\t", index = False)
	task.update_state(state='SUCCESS', meta={'status': 'SEA analysis completed.'}) if task else None
	return sea_results


#### SEA save progress to file ####

def get_progress(task_id: str) -> dict:
	progress_file = f"{os.environ.get('SEA_JOB_DIR')}/{task_id}/.progress"
	if os.path.exists(progress_file):
		with open(progress_file, 'r') as f:
			progress_data = json.load(f)
		return progress_data
	else:
		return {'progress': 0, 'status': 'No progress data found.'}


def save_progress(task_id: str, progress_data: dict) -> None:
	progress_file = f"{os.environ.get('SEA_JOB_DIR')}/{task_id}/.progress"
	# update the progress file with the new data
	old_progress_data = get_progress(task_id)
	updated_progress_data = {**old_progress_data, **progress_data}
	print(updated_progress_data)
	try:
		with open(progress_file, 'w') as f:
			json.dump(updated_progress_data, f)
		return "Progress saved."
	except Exception as e:
		raise Exception(f"Error saving progress: {str(e)}")

def init_progress_file(task_id: str) -> None:
	progress_file = f"{os.environ.get('SEA_JOB_DIR')}/{task_id}/.progress"
	initial_progress_data = {
		'status': 'PENDING',
		'error': None,
		'sea_score_done': False,
		'best_similarity_done': False,
		'heatmap_done': False,
		'failed': False,
	}
	if not os.path.exists(progress_file):
		try:
			with open(progress_file, 'w') as f:
				json.dump(initial_progress_data, f)
			return "Progress file initialized."
		except Exception as e:
			raise Exception(f"Error initializing progress file: {str(e)}")
	else:
		return "Progress file already exists."


################################################################
## Read in and process data
################################################################

def run_sea_analysis(task=None):
	# run sea analysis
	print("Running SEA analysis...")
	list_chembl = np.loadtxt("list_chembl", dtype = str)
	sea_result = sea_analysis_with_multiprocessing("input.smi", list_chembl, task=task)
	return sea_result.to_dict(orient="records")

## Read in and prepare data

if __name__ == "__main__" :

	testFile = sys.argv[1]
	list_chembl = np.loadtxt("list_chembl", dtype = str)
	list_chemb_file = sys.argv[2]
	with open(list_chemb_file, "w") as f :
		for chembl in list_chembl :
			f.write("{}\n".format(chembl))

	## GEV parameter loading
	tc_cutoff      = 0.29
	gev_bin_width  = 0.25
	gev_freq_file  = sys.argv[3] if len(sys.argv) > 3 else None
	gev_cache_file = "gev_params_tc{}_bw{}.tsv".format(tc_cutoff, gev_bin_width)

	if gev_freq_file :
		gev_c, gev_loc, gev_scale = load_gev_params(
			gev_freq_file, gev_cache_file, bin_width = gev_bin_width)
	else :
		gev_c, gev_loc, gev_scale = 0.0, 0.0, 1.0

	sea_result = sea_analysis_with_multiprocessing(testFile, list_chembl,
		gev_c = gev_c, gev_loc = gev_loc, gev_scale = gev_scale)
