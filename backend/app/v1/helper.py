## Import module
from rdkit import Chem
from rdkit.Chem import rdFingerprintGenerator
# from alive_progress import alive_bar
import json
import os
import sys
import time
import multiprocessing as mp
import numpy as np
import pandas as pd
import rdkit.Chem.Scaffolds.MurckoScaffold as murcko
import rdkit.Chem.AllChem as AllChem

np.set_printoptions(suppress=True)



################################################################
## Similarity Ensemble Approach (SEA)
## This is the script used for running SEA analysis
## Compute summarized Tc between test set and known sets
## Plug in to the analysis results -- Z-test
## 	Check original SEA paper
################################################################


################################################################
## Helper function
###############################################################i#

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

	Returns a list of dicts for invalid entries: [{"line": 1, "input": "..."}]
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
			invalid.append({"line": i + 1, "input": line})
	return invalid

def _list_(files, output) :
	'''list files'''
	cmd = '''ls {} > {}'''.format(files, output)
	os.system(cmd)
	return

def _rm_(files) :
	cmd = '''rm -rf {}'''.format(files)
	os.system(cmd)
	return

def _mkdir_(files) :
	cmd = '''mkdir -p {}'''.format(files)
	os.system(cmd)
	return

def tsv2json(input_tsv):
	df = pd.read_csv(input_tsv, sep="\t")
	json_str = df.to_json(orient="records")
	json_data = json.loads(json_str)
	return json_data

################################################################
## Get compounds property
## Molecular weight (number of heavy atoms) and LogP
## Rotatable bond, HBA and HBD
################################################################

def get_smiles_property(smiles, radius = 2, fpSize = 2048, useFeatures = False) : 
	mol_list = []
	for smi in smiles : 
		mol = Chem.MolFromSmiles(smi)
		if not mol is None : mol_list.append(mol)
	
	data = pd.DataFrame()
	data["Name"] = [mol.GetProp("_Name") for mol in mol_list]
	data["HeavyAtoms"] = [mol.GetNumHeavyAtoms() for mol in mol_list]
	data["SMILES"] = [Chem.MolToSmiles(mol) for mol in mol_list]
	data["Bemis"] = [Chem.MolToSmiles(murcko.GetScaffoldForMol(mol)) for mol in mol_list]
	fp = collect_fingerprint(data["SMILES"].to_numpy(), radius, fpSize, useFeatures)
	data = pd.concat([data, fp], axis = 1)

	return data.sort_values(by = ["HeavyAtoms"], ignore_index = True), mol_list

################################################################
## Clean SMILES -- remove salt
################################################################

def remove_salt(smiles) :
	new_smiles = ""
	smiles = smiles.split(".")
	remove = ["O", "S", "N", "F", "Cl", "Br", "I", "[K+]", "[Li+]", "[Ca+2]", "[Zn+2]", "[Na+]", "[F-]", "[Cl-]", "[Br-]", "[I-]", 
		"O=C(O)CC(O)(CC(=O)O)C(=O)O", "O=S(=O)(O)c1ccccc1", "O=C(O)C(F)(F)F", "O=C[O-]", "O=S(=O)(O)CCO",
		"O=C(O)C(=O)O", "O=CO", "CCN(CC)CC", "O=C([O-])C(F)(F)F", "CCCCN(CCCC)CCCC", "[NaH]", "O=S(=O)(O)O",
		"CC(=O)O", "CNC", "NO", "CI", "O=P(O)(O)O", "O=C(O)/C=C/C(=O)O", "[OH-]", "O=C(O)[C@H](O)[C@@H](O)C(=O)O",
		"[O-][Cl+3]([O-])([O-])[O-]", "CS(=O)(=O)O", "[Zn]", "O=[N+]([O-])O", "O=C(O)/C=C\\C(=O)O", 
		"NCCO", "O=C(O)C(O)C(O)C(=O)O", "O=S(=O)([O-])C(F)(F)F", "[O-][Cl+3]([O-])([O-])O", "NC(CO)(CO)CO", "CS(=O)(=O)[O-]"]

	for x in smiles : new_smiles += "{}.".format(x) if x not in remove else ""
	new_smiles = new_smiles[: -1].split(".")
	if len(new_smiles) > 1 :
		length = [len(x) for x in new_smiles]
		return new_smiles[np.argmax(length)]
	else :
		return new_smiles[0] 

################################################################
## Get Fingerprint 
################################################################

## Get morgan fingerprint (EPFC4)
def get_morgan(mol, radius = 2, fpSize = 1024) :
	'''
	ECFP4 -- diameter of 4 == radius of 2, default ECFP use 1024 bits
	'''
	morgan = rdFingerprintGenerator.GetMorganGenerator(radius = radius, fpSize = fpSize) 
	fp = morgan.GetFingerprint(mol)
	return fp.ToList() 

## Get morgan fingerprint with Feature (FPFC4)
def get_morgan_with_feature(mol, radius = 2, fpSize = 1024) :
	'''
	FCFP4 -- diameter of 4 == radius of 2, default ECFP use 1024 bits
	'''
	morgan = rdFingerprintGenerator.GetMorganGenerator(radius = radius, fpSize = fpSize, 
		atomInvariantsGenerator=rdFingerprintGenerator.GetMorganFeatureAtomInvGen()) 
	fp = morgan.GetFingerprint(mol)
	return fp.ToList() 

## Loop through smiles strings and collect all fingerprint
## Return a pandas dataframe
def collect_fingerprint(smiles, radius = 2, fpSize = 1024, useFeatures = False) :
	fps = []
	for smi in smiles:
		mol = Chem.MolFromSmiles(smi)
		fp = get_morgan_with_feature(mol, radius, fpSize) if useFeatures else get_morgan(mol, radius, fpSize)
		fps.append(fp)
	return pd.DataFrame(fps)

################################################################
## Tanimoto Helper Function 
################################################################

## Calculate tanimoto score between two sets of compounds
def calc_tanimoto_sets(mol_setA, mol_setB) :
	mol_setA = np.asarray(mol_setA, dtype=np.float32)
	mol_setB = np.asarray(mol_setB, dtype=np.float32)

	a_and_b = mol_setA @ mol_setB.T

	size_A = np.sum(mol_setA, axis=1, keepdims=True)   # (n_A, 1)
	size_B = np.sum(mol_setB, axis=1, keepdims=True).T  # (1, n_B)

	a_or_b = size_A + size_B - a_and_b
	return (a_and_b / a_or_b).astype(np.float64)

## Calculate tanimoto score between one set of compounds and one reference smile
def calc_tanimoto_ref(mol_setA, mol_ref) :
	mol_setA = np.asarray(mol_setA)
	mol_ref = np.asarray(mol_ref)

	a_and_b = mol_setA @ mol_ref.T

	size_A = np.sum(mol_setA, axis = 1)
	size_B = np.sum(mol_ref)

	a_or_b = size_A + size_B - a_and_b
	return a_and_b / a_or_b 

## Get the maximum similarty between two sets of compounds 
def max_tanimoto(smiles_A, smiles_B) :
	data_A = collect_fingerprint(smiles_A)
	data_B = collect_fingerprint(smiles_B)

	mol_setA = data_A.to_numpy()
	mol_setB = data_B.to_numpy()

	tanimoto = calc_tanimoto_sets(mol_setA, mol_setB)
	return np.amax(tanimoto, axis = 1)

################################################################
## Tanimoto calculation between test and known sets
################################################################

def known_test_tc(known, test, fpSize) :
	'''
	known:		pd.Dataframe	chembl known ligand set
	test:		pd.Dataframe	test compounds set
	fpSize:		int		size of fingerprint
	'''
	fp_cols_str = [str(i) for i in range(fpSize)]
	fp_known = known[fp_cols_str].values.astype(np.float32)
	fp_test = test[np.arange(fpSize)].values.astype(np.float32)

	return calc_tanimoto_sets(fp_known, fp_test)

################################################################
## Calculate SEA Score 
################################################################

def sea_score(similarity, tc_cutoff = 0.57, mean_para = 4.24 * 10 ** -4, std_coef = 4.49 * 10 ** -3, std_exp = 0.665) :
	'''
	similarity	np.ndarray or pd.DataFrame	pairwise Tc score
	'''
	# Handle both numpy arrays and DataFrames
	if hasattr(similarity, 'to_numpy'):
		similarity = similarity.to_numpy()

	size = similarity.shape[0] * similarity.shape[1]
	raw_score = np.sum(similarity[similarity > tc_cutoff])
	z_score = (raw_score - mean_para * size) / (std_coef * size ** std_exp)

	if z_score > 0 :
		if z_score > 500 : z_score = 500
		x = - np.exp(- z_score * np.pi / np.sqrt(6) - 0.577215665)
		p = - (x + x ** 2 / 2 + x ** 3 / 6)
	else :
		p = 1

	return z_score, p

################################################################
## Run SEA Analysis 
## Tc cutoff and Z-test parameters are generated by previous analysis
## See paper :
##	1. 
##	2. 
################################################################



# def sea_analysis(testFile, list_chembl, tc_cutoff = 0.57, mean_para = 4.24 * 10 ** -4, std_coef = 4.49 * 10 ** -3, 
		# std_exp = 0.665, radius = 2, fpSize = 2048, useFeatures = False) :
def sea_analysis(testFile, list_chembl, tc_cutoff = 0.57, mean_para = 4.24 * 10 ** -4, std_coef = 4.49 * 10 ** -3, 
		std_exp = 0.665, radius = 2, fpSize = 2048, useFeatures = False, task=None) :  
	'''
	testFile	str		file name of the test compounds	
	list_chembl	list		list of chembl compounds pathway
	tc_cutoff	float		SEA Tc cutoff
	mean_para	float		Coefficient of expected mean score prediction (y = kx)
	std_coef	float		Coefficient of expected std score prediction (y = ax**b)
	std_exp		float		Exponential of expected std score prediction (y = ax**b)
	radius		int		radius cutoff for fingerprint 
	fpSize		int		size of fingerprint
	useFeatures	boolean		whether use ECFP or FCFP
	'''
	task.update_state(state='PROGRESS', meta={'status': 'Preparing test compounds...'}) if task else None
	with open(testFile, "r") as f:
		smiles_test = f.readlines()
	smiles_test = [f"{remove_salt(x.strip().split()[0])} {x.strip().split()[1]}" for x in smiles_test]
	print(smiles_test)
	data_test, mol_test = get_smiles_property(smiles_test, radius, fpSize, useFeatures)
	saveFolder = "result_{}".format(testFile.split(".")[0])
	_mkdir_(saveFolder)

	target_name = []
	z_score_summary = []
	p_value_summary = []
	# with alive_bar(len(list_chembl)) as bar :
	# replace progress bar with simple loop to keep track of progress
	count = 0
	# time this loop
	print("Starting SEA analysis loop...")
	start_time = time.time()
	for idx in np.arange(len(list_chembl)) :
		count += 1
		new_percent_done = int((count / len(list_chembl)) * 100)
		print(f"Processing known set {idx + 1} of {len(list_chembl)} ({new_percent_done}%)")
		task.update_state(state='PROGRESS', 
							meta={'status': f'Processing known set {idx + 1} of {len(list_chembl)}',
			 						'progress': new_percent_done		
			 				}) if task else None
	
		known = pd.read_csv("{}".format(list_chembl[idx]), sep = "\t", index_col = 0)
		similarity = known_test_tc(known, data_test, fpSize) 
		result = pd.DataFrame(np.round(similarity, 4), index = known["molecule_chembl_id"].tolist(), 
			columns = data_test["Name"].tolist())

		z_score, p_value = sea_score(result, tc_cutoff, mean_para, std_coef, std_exp)
		target_name.append(known["target_name"].tolist()[0])
		z_score_summary.append(z_score)
		p_value_summary.append(p_value)
			# bar()
	end_time = time.time()
	print(f"SEA analysis loop time: {end_time - start_time} seconds")
	sea_result = pd.DataFrame()
	sea_result["Target chembl id"] = list_chembl
	sea_result["Z score"] = np.round(z_score_summary, 2)
	sea_result["p(P value)"] = np.round(- np.log10(np.array(p_value_summary)), 4)
	sea_result["Target name"] = target_name
	sea_result = sea_result.sort_values(by = ["p(P value)"], ignore_index = True) 
	sea_result = sea_result[sea_result["p(P value)"] >= 3]

	# with alive_bar(sea_result.shape[0])  as bar :
	count = 0
	start_time = time.time()
	print("Starting detailed results loop...")
	for idx in np.arange(sea_result.shape[0]) :
		count += 1
		new_percent_done = int((count / sea_result.shape[0]) * 100)
		print(f"Calculating detailed results {new_percent_done}% done")
		task.update_state(state='PROGRESS', 
							meta={
								'status': f'Calculating detailed results {new_percent_done}% done',
								'progress': new_percent_done
								}
								) if task else None
		known = pd.read_csv("{}".format(sea_result["Target chembl id"].tolist()[idx]), sep = "\t", index_col = 0)
		similarity = known_test_tc(known, data_test, fpSize) 
		result = pd.DataFrame(np.round(similarity, 4), index = known["molecule_chembl_id"].tolist(), 
			columns = data_test["Name"].tolist())
		
		result = result.T
		known.index = known["molecule_chembl_id"].tolist()
		best_similarity = pd.DataFrame()
		best_similarity["Name"] = list(result.index)
		best_similarity["SMILES"] = data_test["SMILES"].tolist()
		best_similarity["Target Chembl Id"] = [known["target_chembl_id"].tolist()[0]] * best_similarity.shape[0]
		best_similarity["Target Name"] = [known["target_name"].tolist()[0]] * best_similarity.shape[0]
		best_similarity["Most similar known"] = list(result.idxmax(axis = "columns"))
		best_similarity["Tanimoto Coefficient"] = list(result.max(axis = 1))
		best_similarity["Known Hit SMILES"] = [known.loc["{}".format(name), "canonical_smiles"] for name in result.idxmax(axis = "columns")] 
		best_similarity = best_similarity.sort_values(by = ["Tanimoto Coefficient"], ascending = False, ignore_index = True)
		best_similarity = best_similarity[best_similarity["Tanimoto Coefficient"] >= 0.7]
		best_similarity.to_csv("{}/best_similar_{}_{}.tsv".format(saveFolder, testFile.split(".")[0], known["target_chembl_id"].tolist()[0]), sep = "\t", index = False)

			# Get top 10 similarity matrix

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
		similarity_matrix["Tanimoto Score"] = summary_tc
		similarity_matrix["SMILES"] = summary_ref_smiles
		similarity_matrix["Known Hit ID"] = summary_known
		similarity_matrix["Known Hit SMILES"] = summary_known_smiles
		similarity_matrix = similarity_matrix.sort_values(by = ["Name", "Tanimoto Score"], ascending = [True, False], ignore_index = True)
		similarity_matrix.to_csv("{}/top10_{}_{}.tsv".format(saveFolder, testFile.split(".")[0], known["target_chembl_id"].tolist()[0]), sep = "\t", index = False)

			# bar()
	end_time = time.time()
	print(f"Detailed results loop time: {end_time - start_time} seconds")
	# remove the path in target chembl id
	sea_result["Target chembl id"] = [x.split("/")[-1].split(".")[0] for x in sea_result["Target chembl id"].tolist()]
	sea_result = sea_result.sort_values(by = ["p(P value)"], ignore_index = True) 
	sea_result.to_csv("{}/sea_result.tsv".format(saveFolder), sep = "\t", index = False)
	task.update_state(state='SUCCESS', meta={'status': 'SEA analysis completed.'}) if task else None

	return sea_result

def calculate_sea_results(	input,
						  	target,
							tc_cutoff = 0.57, 
						  	mean_para = 4.24 * 10 ** -4, 
						  	std_coef = 4.49 * 10 ** -3, 
							std_exp = 0.665, 
							radius = 2, 
							fpSize = 2048, 
							useFeatures = False) :
	known = pd.read_csv("{}".format(target), sep = "\t", index_col = 0)
	similarity = known_test_tc(known, input, fpSize)
	result = pd.DataFrame(np.round(similarity, 4), index = known["molecule_chembl_id"].tolist(), 
		columns = input["Name"].tolist())
	z_score, p_value = sea_score(result, tc_cutoff, mean_para, std_coef, std_exp)
	target_name = known["target_name"].tolist()[0]
	return target_name, z_score, p_value

def calculate_best_similarity(input, target,
							  	saveFolder,
							  	tc_cutoff = 0.57, 
						  		mean_para = 4.24 * 10 ** -4, 
								std_coef = 4.49 * 10 ** -3, 
								std_exp = 0.665, 
								radius = 2, 
								fpSize = 2048, 
								useFeatures = False) :
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
									  	tc_cutoff = 0.57, 
									  	mean_para = 4.24 * 10 ** -4, 
									  	std_coef = 4.49 * 10 ** -3, 
										std_exp = 0.665, 
										radius = 2, 
										fpSize = 2048, 
										useFeatures = False, 
										task=None) :
	task.update_state(state='PROGRESS', meta={'status': 'Preparing test compounds...'}) if task else None
	with open(testFile, "r") as f:
		smiles_test = f.readlines()
	smiles_test = [f"{remove_salt(x.strip().split()[0])} {x.strip().split()[1]}" for x in smiles_test]
	data_test, mol_test = get_smiles_property(smiles_test, radius, fpSize, useFeatures)
	saveFolder = "result_{}".format(testFile.split(".")[0])
	_mkdir_(saveFolder)

	target_name = []
	z_score_summary = []
	p_value_summary = []
	# with alive_bar(len(list_chembl)) as bar :
	# replace progress bar with simple loop to keep track of progress
	count = 0
	# time this loop
	print("Starting SEA analysis loop...")
	start_time = time.time()
	# replace for loop with multiprocessing pool
	with mp.Pool(30) as pool:
		results = [pool.apply_async(calculate_sea_results, args=(data_test, target, tc_cutoff, mean_para, std_coef, std_exp, radius, fpSize, useFeatures)) for target in list_chembl]
		for idx, res in enumerate(results):
			count += 1
			new_percent_done = int((count / len(list_chembl)) * 100)
			print(f"multiprocessing known set {idx + 1} of {len(list_chembl)} ({new_percent_done}%)")
			task.update_state(state='PROGRESS', 
								meta={'status': f'multiprocessing known set {idx + 1} of {len(list_chembl)}',
				 						'progress': new_percent_done		
				 				}) if task else None
			target_n, z_s, p_v = res.get()
			target_name.append(target_n)
			z_score_summary.append(z_s)
			p_value_summary.append(p_v)
	end_time = time.time()
	print(f"SEA analysis loop time: {end_time - start_time} seconds")

	sea_results = pd.DataFrame()
	sea_results["Target_chembl_id"] = list_chembl
	sea_results["Z_score"] = np.round(z_score_summary, 2)
	sea_results["p(P_value)"] = np.round(- np.log10(np.array(p_value_summary)), 4)
	sea_results["Target_name"] = target_name
	sea_results = sea_results.sort_values(by = ["p(P_value)"], ignore_index = True) 
	sea_results = sea_results[sea_results["p(P_value)"] >= 3]

	print("Starting detailed results loop...")
	count = 0
	with mp.Pool(30) as pool:
		results = [pool.apply_async(calculate_best_similarity, args=(data_test, target, saveFolder, tc_cutoff, mean_para, std_coef, std_exp, radius, fpSize, useFeatures)) for target in sea_results["Target_chembl_id"].tolist()]
		# wait for all to finish
		for idx, res in enumerate(results):
			count += 1
			new_percent_done = int((count / sea_results.shape[0]) * 100)
			print(f"multiprocessing detailed results {new_percent_done}% done")
			task.update_state(state='PROGRESS', 
								meta={
									'status': f'multiprocessing detailed results {new_percent_done}% done',
									'progress': new_percent_done
									}
									) if task else None
			res.get()
	task.update_state(state='SUCCESS', meta={'status': 'SEA analysis completed.'}) if task else None
	return sea_results


#### SEA save progress to file ####

def get_progress(task_id: str) -> dict:
	progress_file = f"{os.environ.get("SEA_JOB_DIR")}/{task_id}/.progress"
	if os.path.exists(progress_file):
		with open(progress_file, 'r') as f:
			progress_data = json.load(f)
		return progress_data
	else:
		return {'progress': 0, 'status': 'No progress data found.'}



def save_progress(task_id: str, progress_data: dict) -> None:
	progress_file = f"{os.environ.get("SEA_JOB_DIR")}/{task_id}/.progress"
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
	progress_file = f"{os.environ.get("SEA_JOB_DIR")}/{task_id}/.progress"
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
	smiles_test = np.loadtxt(testFile, comments = None, dtype = str)
	list_chembl = np.loadtxt("list_chembl", dtype = str)
	sea_result = sea_analysis_with_multiprocessing(testFile, list_chembl)


