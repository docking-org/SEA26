## Import module
from rdkit import Chem
from rdkit.Chem import rdFingerprintGenerator
from alive_progress import alive_bar
from scipy.stats import genextreme
import os
import sys
import numpy as np
import pandas as pd
import rdkit.Chem.Scaffolds.MurckoScaffold as murcko
np.set_printoptions(suppress=True)


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
	data = pd.DataFrame()

	for idxMol in np.arange(len(smiles)) :
		mol = Chem.MolFromSmiles(smiles[idxMol])
		fingerprint = get_morgan_with_feature(mol, radius, fpSize) if useFeatures else get_morgan(mol, radius, fpSize)
		new_row = pd.Series(fingerprint)
		data = pd.concat([data, new_row], ignore_index = True, axis = 1)

	data = data.T
	return data

################################################################
## Tanimoto Helper Function
################################################################

## Calculate tanimoto score between two sets of compounds
def calc_tanimoto_sets(mol_setA, mol_setB) :
	# Previous version materialised size_A and size_B as full (n_A, n_B)
	# matrices via np.ones((...)) * count, and also computed an unused
	# mol_setB @ mol_setB.T product.  For (100k x 5k) inputs that was
	# ~12 GB of allocations and one extra n_B^2 matmul per call.  Using
	# broadcasting (count_A[:, None] + count_B[None, :]) only allocates
	# the single output (n_A, n_B) matrix needed for a_or_b, and the
	# unused dist is dropped entirely.
	mol_setA = np.asarray(mol_setA)
	mol_setB = np.asarray(mol_setB)

	a_and_b  = mol_setA @ mol_setB.T                       # (n_A, n_B)
	count_A  = np.sum(mol_setA, axis = 1)                  # (n_A,)
	count_B  = np.sum(mol_setB, axis = 1)                  # (n_B,)
	a_or_b   = count_A[:, None] + count_B[None, :] - a_and_b
	return a_and_b / a_or_b

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

	fp_known = known[np.arange(fpSize).astype(str)].to_numpy()
	fp_test = test[np.arange(fpSize)].to_numpy()

	return calc_tanimoto_sets(fp_known, fp_test)

################################################################
## Generalized extreme value parameter fit
## Fit on first run from the precomputed null-set z-score histogram
## and cache the (shape, loc, scale) tuple to disk.  Subsequent runs
## load the cached params (cheap) instead of refitting (~ minutes on
## the full freq file).  Delete the cache file or pass refit = True
## to force a refit if the underlying freq file changes.
##
## bin_width must match the value used in zscore_dist.py when the
## winning EVD was selected -- the parameterization fitted on
## bin-grouped samples (group_data + np.repeat(z, count)), not on
## the raw 0.001-resolution z-scores in {tc}_orig_freq.tsv.  Default
## 0.25 mirrors zscore_dist.py's default; pass a different value to
## reproduce alternate parameterization runs.
################################################################

def group_data(data, bin_width) :
	'''
	data		pd.DataFrame	{tc}_orig_freq.tsv (columns: z_score, count)
	bin_width	float		uniform bin width over [-5, 10]
	Returns a grouped DataFrame with one row per non-empty bin.
	Mirrors zscore_dist.group_data so the fit sees identical samples.
	'''
	z_min, z_max = -5, 10

	z_score = data["z_score"].to_numpy()
	boolean = (z_score >= z_min) & (z_score <= z_max)
	sample  = np.arange(z_min, z_max, bin_width).round(4)

	count   = data["count"].to_numpy()[boolean]
	z_score = z_score[boolean]

	bin_idx = np.clip(np.digitize(z_score, sample) - 1, 0, len(sample) - 1).astype(int)
	z_group = np.bincount(bin_idx, weights = count, minlength = len(sample))

	grouped = pd.DataFrame()
	grouped["count"]   = z_group
	grouped["z_score"] = sample
	grouped = grouped[grouped["count"] != 0].reset_index(drop = True)
	return grouped

def fit_gev_from_freq(freq_file, bin_width = 0.25) :
	'''
	freq_file	str	path to {tc}_orig_freq.tsv produced by zscore_dist.py
	bin_width	float	uniform bin width to apply before fitting
	Returns shape, loc, scale for scipy.stats.genextreme.
	'''
	data    = pd.read_csv(freq_file, sep = "\t", index_col = 0)
	grouped = group_data(data, bin_width)
	z_score = grouped["z_score"].to_numpy()
	count   = grouped["count"].astype(int).to_numpy()
	samples = np.repeat(z_score, count)
	c, loc, scale = genextreme.fit(samples)
	return c, loc, scale

def load_gev_params(freq_file, cache_file, bin_width = 0.25, refit = False) :
	'''
	freq_file	str	path to {tc}_orig_freq.tsv produced by zscore_dist.py
	cache_file	str	path to the cached params TSV (created on first run)
	bin_width	float	uniform bin width to apply before fitting
	refit		bool	force a refit and overwrite the cache
	Returns shape, loc, scale for scipy.stats.genextreme.

	The cache stores the bin_width that was used so that changing
	bin_width invalidates the cache automatically.
	'''
	if (not refit) and os.path.exists(cache_file) :
		params = pd.read_csv(cache_file, sep = "\t", index_col = 0)
		cached_bw = float(params["bin_width"].iloc[0]) if "bin_width" in params.columns else None
		if cached_bw is not None and abs(cached_bw - bin_width) > 1e-9 :
			print("Cache bin_width = {} != requested {}; refitting".format(cached_bw, bin_width))
		else :
			c     = float(params["c"]    .iloc[0])
			loc   = float(params["loc"]  .iloc[0])
			scale = float(params["scale"].iloc[0])
			print("Loaded cached genextreme params from {}".format(cache_file))
			return c, loc, scale

	print("Fitting genextreme on {} at bin_width = {} (cache miss) ...".format(freq_file, bin_width))
	c, loc, scale = fit_gev_from_freq(freq_file, bin_width)
	pd.DataFrame({
		"c"          : [c],
		"loc"        : [loc],
		"scale"      : [scale],
		"bin_width"  : [bin_width],
		"freq_file"  : [freq_file],
	}).to_csv(cache_file, sep = "\t")
	print("Saved genextreme params to {}".format(cache_file))
	return c, loc, scale

################################################################
## Uniprot / sequence annotation
## Left-join sea_result on CHEMBL ID against a lookup TSV with columns
## (target_chembl_id, target_name, organism, uniprot_accession, sequence)
## so the final output records the Uniprot accession and protein sequence
## for each surviving target.  Missing entries land as NaN.
################################################################

def add_uniprot_info(sea_result, lookup_file) :
	'''
	sea_result	pd.DataFrame	must have a "CHEMBL ID" column
	lookup_file	str		path to uniprot_chembl_id.tsv
	Returns sea_result with two extra columns: "Uniprot ID" and "Sequence".
	No-op (with warning + NaN columns) if the lookup file is missing.
	'''
	if not os.path.exists(lookup_file) :
		print("Warning: lookup file {} not found; skipping Uniprot annotation".format(lookup_file))
		sea_result["Uniprot ID"] = np.nan
		sea_result["Sequence"]   = np.nan
		return sea_result
	lookup = pd.read_csv(lookup_file, sep = "\t", index_col = 0)
	sea_result = sea_result.merge(
		lookup[["target_chembl_id", "uniprot_accession", "sequence"]],
		left_on = "CHEMBL ID", right_on = "target_chembl_id", how = "left",
	)
	sea_result = sea_result.drop(columns = ["target_chembl_id"])
	sea_result = sea_result.rename(columns = {
		"uniprot_accession" : "Uniprot ID",
		"sequence"          : "Sequence",
	})
	return sea_result

################################################################
## Standard-Gumbel Taylor fallback (original SEA paper)
##
## Used when the fitted genextreme.logsf is non-finite (e.g. gev_c > 0
## puts a finite upper bound on the support, and a real-hit z exceeds
## it -- scipy then returns sf = 0 and logsf = -inf, which would write
## -log10(p) = +inf to the output).  The original SEA paper assumed a
## standard Gumbel with mean = 0, std = 1 (loc = -gamma*sqrt(6)/pi,
## scale = sqrt(6)/pi) and used the Taylor expansion of 1 - exp(x) for
## x = -exp(-z*pi/sqrt(6) - gamma).  Reproducing that formula in log
## form gives a finite logsf for any finite z, so the deep-tail
## p-value stays usable.
################################################################

def taylor_gumbel_logsf(z) :
	'''
	Original SEA paper's standard-Gumbel Taylor approximation of logsf.
	Asymptotically logsf(z) -> -(z*pi/sqrt(6) + gamma) as z -> infinity.
	Returns log(p) where p is the original code's
		p = -(x + x^2/2 + x^3/6),  x = -exp(-z*pi/sqrt(6) - gamma).
	'''
	GAMMA = 0.577215664901532
	y = z * np.pi / np.sqrt(6) + GAMMA
	# Asymptotic branch: for large y, exp(-y) underflows but logsf -> -y is exact.
	if y > 50 :
		return -y
	x = -np.exp(-y)
	p = -(x + x ** 2 / 2 + x ** 3 / 6)
	if p > 0 :
		return np.log(p)
	return -y

################################################################
## Calculate SEA Score
################################################################

def sea_score(similarity, tc_cutoff = 0.29, mean_slope = 4.3304e-04, mean_intercept = -0.01631567,
		std_a = 0.0047, std_b = 0.6409, std_c = 0.4999,
		gev_c = 0.0, gev_loc = 0.0, gev_scale = 1.0) :
	'''
	similarity	np.ndarray or pd.DataFrame	pairwise Tc score
	Returns (z_score, p, log_sf, raw_score, size).  raw_score and size
	are returned so the caller can avoid recomputing them for the result
	table -- in tight loops over many large similarity matrices this
	saves one mask + one sum per iteration.
	'''
	if hasattr(similarity, "to_numpy") :
		similarity = similarity.to_numpy()
	size = similarity.shape[0] * similarity.shape[1]
	raw_score = float(np.sum(similarity[similarity > tc_cutoff]))

	expected_mean = mean_slope * size + mean_intercept
	expected_std  = std_a * size ** std_b + std_c
	z_score       = (raw_score - expected_mean) / expected_std

	# logsf for numerical stability in the upper tail (sf can underflow to 0).
	log_sf = genextreme.logsf(z_score, gev_c, loc = gev_loc, scale = gev_scale)
	# Fall back to the original SEA paper's standard-Gumbel Taylor form
	# when the fitted GEV's logsf is non-finite -- happens for gev_c > 0
	# above the upper bound z = gev_loc + gev_scale / gev_c.  This keeps
	# -log10(p) finite for arbitrarily large z (extreme hits).
	if not np.isfinite(log_sf) :
		log_sf = taylor_gumbel_logsf(z_score)
	p      = np.exp(log_sf) if log_sf > -700 else 0.0

	return z_score, p, log_sf, raw_score, size

################################################################
## Run SEA Analysis
## Tc cutoff and EVD parameters are generated by previous analysis
## See paper :
##	1.
##	2.
################################################################

def sea_analysis(testFile, list_chembl, tc_cutoff = 0.29, mean_slope = 4.3304e-04, mean_intercept = -0.01631567,
		std_a = 0.0047, std_b = 0.6409, std_c = 0.4999,
		gev_c = 0.0, gev_loc = 0.0, gev_scale = 1.0,
		p_threshold = 0.01, high_tc_threshold = 0.95,
		lookup_file = "uniprot_chembl_id.tsv",
		radius = 2, fpSize = 2048, useFeatures = False) :
	'''
	testFile		str	file name of the test compounds
	list_chembl		list	list of chembl compounds pathway
	tc_cutoff		float	SEA Tc cutoff
	mean_slope		float	Slope     of expected mean score (y = m*x + b)
	mean_intercept		float	Intercept of expected mean score (y = m*x + b)
	std_a			float	Coefficient  of expected std score (y = a*x**b + c)
	std_b			float	Exponent     of expected std score (y = a*x**b + c)
	std_c			float	Offset       of expected std score (y = a*x**b + c)
	gev_c			float	Shape parameter for scipy.stats.genextreme
	gev_loc			float	Location parameter for scipy.stats.genextreme
	gev_scale		float	Scale parameter for scipy.stats.genextreme
	p_threshold		float	Maximum p-value (actual p, not -log10) to keep
	high_tc_threshold	float	A target with max pairwise Tc strictly greater
					than this is kept regardless of p_threshold.
					Catches near-identical hits (e.g. literature
					ligand match) the EVD might score modestly.
	lookup_file		str	Path to TSV with columns target_chembl_id,
					target_name, organism, uniprot_accession,
					sequence.  Used to annotate sea_result with
					Uniprot ID and protein sequence.
	radius			int	radius cutoff for fingerprint
	fpSize			int	size of fingerprint
	useFeatures		boolean	whether use ECFP or FCFP
	'''

	smiles_test = np.loadtxt(testFile, comments = None, dtype = str)
	if smiles_test.ndim > 1 :
		smiles_test = ["{} {}".format(remove_salt(smiles_test[idx, 0]), smiles_test[idx, 1]) for idx in np.arange(smiles_test.shape[0])]
	else :
		smiles_test = ["{} {}".format(remove_salt(smiles_test[0]), smiles_test[1])]
	data_test, mol_test = get_smiles_property(smiles_test, radius, fpSize, useFeatures)
	saveFolder = "result_{}".format(testFile.split(".")[0])
	_mkdir_(saveFolder)

	target_name       = []
	target_species    = []
	target_chembl_ids = []
	z_score_summary = []
	p_value_summary = []
	log_sf_summary  = []
	raw_score_summary = []
	size_summary      = []
	best_tc_summary   = []
	with alive_bar(len(list_chembl)) as bar :
		for idx in np.arange(len(list_chembl)) :
			known = pd.read_csv("{}".format(list_chembl[idx]), sep = "\t", index_col = 0)
			# known_test_tc returns a numpy array already; keep it as numpy
			# for the score / max calculations to avoid building a labelled
			# DataFrame here (the second loop re-creates one with labels for
			# the per-target output TSVs).  Dropping that allocation per
			# iteration shaved off ~half of the per-loop time on large
			# (~100k known x ~5k test) matrices.
			similarity = known_test_tc(known, data_test, fpSize)
			similarity = np.round(similarity, 4)

			best_tc = float(np.max(similarity)) if similarity.size > 0 else 0.0

			# sea_score now returns raw_score and size too, so we don't redo
			# the mask + sum just to populate the result table.
			z_score, p_value, log_sf, raw_score, size = sea_score(
				similarity, tc_cutoff, mean_slope, mean_intercept,
				std_a, std_b, std_c, gev_c, gev_loc, gev_scale)
			target_name.append(known["target_name"].tolist()[0])
			target_chembl_ids.append(known["target_chembl_id"].tolist()[0])
			z_score_summary.append(z_score)
			p_value_summary.append(p_value)
			log_sf_summary.append(log_sf)
			raw_score_summary.append(raw_score)
			size_summary     .append(size)
			best_tc_summary  .append(best_tc)
			bar()

	# -log10(p) computed from logsf to keep precision in the deep tail.
	neg_log10_p = - np.array(log_sf_summary) / np.log(10)

	sea_result = pd.DataFrame()
	sea_result["Target chembl id"] = list_chembl           # path to known TSV (used by 2nd loop)
	sea_result["CHEMBL ID"]      = target_chembl_ids       # actual CHEMBL accession (lookup key)
	sea_result["Size"]           = size_summary
	sea_result["Raw score"]      = np.round(raw_score_summary, 4)
	sea_result["Best Tc"]        = np.round(best_tc_summary, 4)
	sea_result["Z score"]        = np.round(z_score_summary, 2)
	sea_result["p value"]        = p_value_summary
	sea_result["p(P value)"]     = np.round(neg_log10_p, 4)
	sea_result["Target name"]    = target_name

	# Annotate with Uniprot accession and sequence via lookup.
	sea_result = add_uniprot_info(sea_result, lookup_file)
	sea_result = sea_result.sort_values(by = ["p(P value)"], ascending = False, ignore_index = True)

	# Always save the unfiltered table so the user can see all p-values, even
	# the ones below threshold. Useful for sanity-checking the GEV fit when the
	# filtered table comes back empty or short.
	sea_result.to_csv("{}/sea_result_all.tsv".format(saveFolder), sep = "\t")

	# GEV diagnostics -- if the test z-scores never reach the upper tail of
	# the fitted GEV, p-values stay close to 1 and few rows clear the
	# p_threshold filter.  Print a quick summary so this is visible.
	z_arr   = np.asarray(z_score_summary)
	p_arr   = np.asarray(p_value_summary)
	tc_arr  = np.asarray(best_tc_summary)
	pass_p  = p_arr  <= p_threshold
	pass_tc = tc_arr >  high_tc_threshold
	if gev_c > 0 :
		upper_bound = gev_loc + gev_scale / gev_c
		print("GEV upper bound (c > 0): z = {:.3f}".format(upper_bound))
	print("Z score : min = {:.3f}, max = {:.3f}, median = {:.3f}".format(
		float(np.min(z_arr)), float(np.max(z_arr)), float(np.median(z_arr))))
	print("-log10(p) : min = {:.3f}, max = {:.3f}".format(
		float(np.min(neg_log10_p)), float(np.max(neg_log10_p))))
	print("p value <= {}     : {}".format(p_threshold,        int(np.sum(pass_p))))
	print("Best Tc >  {}    : {}".format(high_tc_threshold,   int(np.sum(pass_tc))))
	print("Combined (p OR Tc): {}".format(int(np.sum(pass_p | pass_tc))))

	# Keep targets that either pass the p-value threshold OR have at least
	# one near-identical pairwise Tc (high_tc_threshold rescue).
	sea_result = sea_result[
		(sea_result["p value"] <= p_threshold) |
		(sea_result["Best Tc"] >  high_tc_threshold)
	]
	print(sea_result)

	with alive_bar(sea_result.shape[0])  as bar :
		for idx in np.arange(sea_result.shape[0]) :
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
			#best_similarity = best_similarity[best_similarity["Tanimoto Coefficient"] >= 0.7]
			best_similarity.to_csv("{}/best_similar_{}_{}.tsv".format(saveFolder, testFile.split(".")[0], known["target_chembl_id"].tolist()[0]), sep = "\t")

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
			similarity_matrix.to_csv("{}/top10_{}_{}.tsv".format(saveFolder, testFile.split(".")[0], known["target_chembl_id"].tolist()[0]), sep = "\t")

			bar()

	sea_result = sea_result.sort_values(by = ["p(P value)"], ignore_index = True)
	sea_result.to_csv("{}/sea_result.tsv".format(saveFolder), sep = "\t")

	return sea_result

################################################################
## Read in and process extreme value distribution 
################################################################

## Input variables
testFile     = sys.argv[1]
list_chembl  = np.loadtxt("{}".format(sys.argv[2]), dtype = str)

## SEA Tc cutoff -- smallest GoF error from zscore_dist.py
tc_cutoff      = 0.29

## Mean fit  : E[score]   = mean_slope * size + mean_intercept
## See param_avg_tanimoto.tsv (row tc = 0.29)
mean_slope     = 4.3304e-04
mean_intercept = -0.01631567

## Std fit   : Std[score] = std_a * size ** std_b + std_c
## See param_std_tanimoto.tsv (row tc = 0.29)
std_a          = 0.0047
std_b          = 0.6409
std_c          = 0.4999

## p-value   : scipy.stats.genextreme survival function
## c, loc, scale fitted once from the null-set z-score histogram at
## Tc = 0.29 and cached to gev_cache_file for subsequent runs.  At
## Tc = 0.29, genextreme has the smallest chi^2_Neyman among the 6
## EVDs tried in zscore_dist.py.
##
## gev_freq_file is the unbinned, Tc-filtered raw histogram and does
## not depend on bin_width (orig_freq.tsv is byte-identical across
## result_binwidth_*/ runs).  bin_width controls only the binning
## applied before fitting and is encoded in the cache filename, so
## changing gev_bin_width auto-invalidates the cache and refits.
## Set refit_gev = True to force a refit even when the cache exists.
gev_bin_width   = 0.25
gev_freq_file   = "../Parameterization/result_binwidth_0.25/{}_orig_freq.tsv".format(tc_cutoff)
gev_cache_file  = "gev_params_tc{}_bw{}.tsv".format(tc_cutoff, gev_bin_width)
refit_gev       = False
gev_c, gev_loc, gev_scale = load_gev_params(
	gev_freq_file, gev_cache_file, bin_width = gev_bin_width, refit = refit_gev)
print("Genextreme @ Tc = {}, bin_width = {}: c = {:.6f}, loc = {:.6f}, scale = {:.6f}".format(
	tc_cutoff, gev_bin_width, gev_c, gev_loc, gev_scale))

## Filtering thresholds for the saved sea_result table.
##	p_threshold        : keep targets with p-value <= this (actual p, not
##	                     -log10(p)).  Default 0.01 corresponds to the
##	                     -log10(p) >= 2.0 cut used in the SEA paper.
##	high_tc_threshold  : keep targets whose maximum pairwise Tc strictly
##	                     exceeds this regardless of p.  Catches near
##	                     identical hits (e.g. literature ligand) the EVD
##	                     might score modestly.
p_threshold       = 0.01
high_tc_threshold = 0.95

## Lookup TSV used to annotate the final sea_result with Uniprot ID and
## protein sequence.  Columns required: target_chembl_id, uniprot_accession,
## sequence.  Missing entries land as NaN.
lookup_file       = "uniprot_chembl_id.tsv"

## Fingerprint settings
radius      = 2
fpSize      = 2048
useFeatures = False

################################################################
## SEA predictions
################################################################

sea_result = sea_analysis(testFile, list_chembl,
	tc_cutoff         = tc_cutoff,
	mean_slope        = mean_slope,
	mean_intercept    = mean_intercept,
	std_a             = std_a,
	std_b             = std_b,
	std_c             = std_c,
	gev_c             = gev_c,
	gev_loc           = gev_loc,
	gev_scale         = gev_scale,
	p_threshold       = p_threshold,
	high_tc_threshold = high_tc_threshold,
	lookup_file       = lookup_file,
	radius            = radius,
	fpSize            = fpSize,
	useFeatures       = useFeatures)
print(sea_result)


