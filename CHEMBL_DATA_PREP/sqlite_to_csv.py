## Import module
import sys
import os
import requests
import tqdm
import sqlite3
import numpy as np
import pandas as pd
from scipy.stats import genextreme
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from rdkit import Chem
from rdkit.Chem import AllChem, DataStructs, Descriptors, rdFingerprintGenerator


AGGREGATORS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aggregators_050219.tsv") 

_MORGAN_GEN      = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=1024)
_MORGAN_GEN_2048 = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)

# Multipliers to convert each unit to nM (matching R script activity_unit_transforms)
ACTIVITY_UNIT_TRANSFORMS = {
    # SI molar units
    'fM': 1e-6, 'pM': 1e-3, 'nM': 1, 'uM': 1e3, 'mM': 1e6, 'M': 1e9,
    # SI moles per ml
    'fmol/ml': 1e-3, 'pmol/ml': 1, 'nmol/ml': 1e3, 'umol/ml': 1e9, 'mmol/ml': 1e12,
    # per mole
    'M-1': 1e-9,
}

# Activity type transforms matching R script activity_type_transforms
# identity: value is already in the given unit
# minus_log: pIC50-style, 10^(9-x) converts to nM
# plus_log:  Log IC50-style, 10^(9+x) converts to nM
def _identity(x):   return x
def _minus_log(x):  return 10 ** (9 - x)
def _plus_log(x):   return 10 ** (9 + x)

ACTIVITY_TYPE_TRANSFORMS = {
    'Kb': _identity, 'pKb': _minus_log, '-Log Kb': _minus_log,
    'Log 1/Kb': _minus_log, 'log(1/Kb)': _minus_log, 'Log Kb': _plus_log, 'logKb': _plus_log,
    'Kd': _identity, 'pKd': _minus_log, '-Log Kd': _minus_log,
    'Log 1/Kd': _minus_log, 'log(1/Kd)': _minus_log, 'Log Kd': _plus_log, 'logKd': _plus_log,
    '-Log KD': _minus_log,
    'Ki': _identity, 'pKi': _minus_log, '-Log Ki': _minus_log,
    'Log 1/Ki': _minus_log, 'log(1/Ki)': _minus_log, 'Log Ki': _plus_log, 'logKi': _plus_log,
    'AC50': _identity, '-Log AC50': _minus_log, 'Log 1/AC50': _minus_log,
    'log(1/AC50)': _minus_log, 'Log AC50': _plus_log, 'logAC50': _plus_log,
    'AD50': _identity, '-Log AD50': _minus_log, 'Log 1/AD50': _minus_log,
    'log(1/AD50)': _minus_log, 'Log AD50': _plus_log, 'logAD50': _plus_log,
    'CC50': _identity, '-Log CC50': _minus_log, 'Log 1/CC50': _minus_log,
    'log(1/CC50)': _minus_log, 'Log CC50': _plus_log, 'logCC50': _plus_log,
    'EC50': _identity, 'pEC50': _minus_log, '-Log EC50': _minus_log,
    'Log 1/EC50': _minus_log, 'log(1/EC50)': _minus_log, 'Log EC50': _plus_log, 'logEC50': _plus_log,
    'EC90': _identity, 'pEC90': _minus_log, '-Log EC90': _minus_log,
    'Log 1/EC90': _minus_log, 'log(1/EC90)': _minus_log, 'Log EC90': _plus_log, 'logEC90': _plus_log,
    'ED50': _identity, 'pED50': _minus_log, '-Log ED50': _minus_log,
    'Log 1/ED50': _minus_log, 'log(1/ED50)': _minus_log, 'Log ED50': _plus_log, 'logED50': _plus_log,
    'GI50': _identity, 'pGI50': _minus_log, '-Log GI50': _minus_log,
    'Log 1/GI50': _minus_log, 'log(1/GI50)': _minus_log, 'Log GI50': _plus_log, 'logGI50': _plus_log,
    'IA50': _identity, 'pIA50': _minus_log, '-Log IA50': _minus_log,
    'Log 1/IA50': _minus_log, 'log(1/IA50)': _minus_log, 'Log IA50': _plus_log, 'logIA50': _plus_log,
    'IC50': _identity, 'pIC50': _minus_log, '-Log IC50': _minus_log,
    'Log 1/IC50': _minus_log, 'log(1/IC50)': _minus_log, 'Log IC50': _plus_log, 'logIC50': _plus_log,
    'IC60': _identity, 'pIC60': _minus_log, '-Log IC60': _minus_log,
    'Log 1/IC60': _minus_log, 'log(1/IC60)': _minus_log, 'Log IC60': _plus_log, 'logIC60': _plus_log,
    'IC70': _identity, 'pIC70': _minus_log, '-Log IC70': _minus_log,
    'Log 1/IC70': _minus_log, 'log(1/IC70)': _minus_log, 'Log IC70': _plus_log, 'logIC70': _plus_log,
    'IC80': _identity, 'pIC80': _minus_log, '-Log IC80': _minus_log,
    'Log 1/IC80': _minus_log, 'log(1/IC80)': _minus_log, 'Log IC80': _plus_log, 'logIC80': _plus_log,
    'IC90': _identity, 'pIC90': _minus_log, '-Log IC90': _minus_log,
    'Log 1/IC90': _minus_log, 'log(1/IC90)': _minus_log, 'Log IC90': _plus_log, 'logIC90': _plus_log,
    'IC95': _identity, 'pIC95': _minus_log, '-Log IC95': _minus_log,
    'Log 1/IC95': _minus_log, 'log(1/IC95)': _minus_log, 'Log IC95': _plus_log, 'logIC95': _plus_log,
    'IC99': _identity, 'pIC99': _minus_log, '-Log IC99': _minus_log,
    'Log 1/IC99': _minus_log, 'log(1/IC99)': _minus_log, 'Log IC99': _plus_log, 'logIC99': _plus_log,
    'IC100': _identity, 'pIC100': _minus_log, '-Log IC100': _minus_log,
    'Log 1/IC100': _minus_log, 'log(1/IC100)': _minus_log, 'Log IC100': _plus_log, 'logIC100': _plus_log,
    'ID50': _identity, 'pID50': _minus_log, '-Log ID50': _minus_log,
    'Log 1/ID50': _minus_log, 'log(1/ID50)': _minus_log, 'Log ID50': _plus_log, 'logID50': _plus_log,
    'LD50': _identity, '-Log LD50': _minus_log, 'Log 1/LD50': _minus_log,
    'log(1/LD50)': _minus_log, 'Log LD50': _plus_log, 'logLD50': _plus_log,
    'MIC': _identity, 'MIC50': _identity, 'MIC90': _identity,
}



################################################################
## This is used to convert CHEMBL sqlite dataset to TSV
## Each TSV file is named by target chembl ID
## EACH TSV file contains the corresponding molecular chembl ID
################################################################

################################################################
## Aggregator filtering using ECFP4 Tanimoto similarity
################################################################

# Module-level state for worker processes (avoids re-pickling fingerprints)
_AGG_FPS = None

def _init_worker(agg_fp_strings):
    global _AGG_FPS
    _AGG_FPS = [DataStructs.CreateFromBitString(s) for s in agg_fp_strings]

def _score_compound(args):
    chembl_id, smi = args
    mol = Chem.MolFromSmiles(smi)
    if mol is None:
        return chembl_id, 0.0, float("nan")
    fp = _MORGAN_GEN.GetFingerprint(mol)
    max_tc = max(DataStructs.BulkTanimotoSimilarity(fp, _AGG_FPS), default=0.0)
    return chembl_id, round(max_tc, 4), Descriptors.MolLogP(mol)

def _build_ecfp4_fps(smiles_list):
    fps = []
    for i, smi in enumerate(smiles_list):
        mol = Chem.MolFromSmiles(smi)
        if mol:
            fps.append((i, _MORGAN_GEN.GetFingerprint(mol)))
    return fps

def filter_aggregators(data, aggregator_file, tc_cutoff=0.7, removed_file="aggregators_removed.tsv"):
    print(f"Loading aggregators from {aggregator_file}")
    agg_df = pd.read_csv(aggregator_file, sep="\t")
    agg_fps = [fp for _, fp in _build_ecfp4_fps(agg_df["smiles"].dropna().tolist())]
    print(f"  {len(agg_fps)} aggregator fingerprints built")

    # Compute per-compound max Tc to any aggregator and ALogP
    unique_cpds = data[["molecule_chembl_id", "canonical_smiles"]].drop_duplicates("molecule_chembl_id").reset_index(drop=True)
    cpd_fps = _build_ecfp4_fps(unique_cpds["canonical_smiles"].tolist())

    cpd_max_tc = {}
    cpd_alogp = {}
    agg_fp_strings = [fp.ToBitString() for fp in agg_fps]
    tasks = [(row.molecule_chembl_id, row.canonical_smiles)
             for row in unique_cpds.itertuples(index=False)]
    with ProcessPoolExecutor(
            max_workers=os.cpu_count(),
            initializer=_init_worker,
            initargs=(agg_fp_strings,)) as pool:
        for chembl_id, max_tc, alogp in tqdm.tqdm(
                pool.map(_score_compound, tasks, chunksize=500),
                total=len(tasks), desc="Computing aggregator similarity"):
            cpd_max_tc[chembl_id] = max_tc
            cpd_alogp[chembl_id]  = alogp

    # Map compound-level values onto activity rows
    data = data.copy()
    data["_max_tc"] = data["molecule_chembl_id"].map(cpd_max_tc).fillna(0.0)
    data["_alogp"]  = data["molecule_chembl_id"].map(cpd_alogp)

    # Match R logic: remove a row only if the compound is aggregator-like (Tc >= cutoff)
    # AND greasy (ALogP > 3.5) AND not very potent (activity > 1000 nM).
    # Polar compounds (ALogP <= 3.5) and very potent hits (activity <= 1000 nM) are kept
    # even if they resemble a known aggregator.
    remove_mask = (
        (data["_max_tc"] >= tc_cutoff) &
        (data["_alogp"]  > 3.5) &
        (data["activity_nM"] > 1000)
    )

    removed = data[remove_mask][["molecule_chembl_id", "canonical_smiles", "_max_tc", "_alogp", "activity_nM"]].copy()
    removed = removed.rename(columns={"_max_tc": "max_tc_to_aggregator", "_alogp": "ALogP"})
    removed.to_csv(removed_file, sep="\t", index=False)
    print(f"Aggregator filtering: removed {remove_mask.sum()} rows "
          f"({removed['molecule_chembl_id'].nunique()} unique compounds written to {removed_file})")

    data = data[~remove_mask].drop(columns=["_max_tc", "_alogp"]).reset_index(drop=True)
    return data

################################################################
## Fingerprint generation
################################################################

_SALT_FRAGMENTS = {
    "O", "S", "N", "F", "Cl", "Br", "I", "[K+]", "[Li+]", "[Ca+2]", "[Zn+2]", "[Na+]",
    "[F-]", "[Cl-]", "[Br-]", "[I-]", "O=C(O)CC(O)(CC(=O)O)C(=O)O",
    "O=S(=O)(O)c1ccccc1", "O=C(O)C(F)(F)F", "O=C[O-]", "O=S(=O)(O)CCO",
    "O=C(O)C(=O)O", "O=CO", "CCN(CC)CC", "O=C([O-])C(F)(F)F", "CCCCN(CCCC)CCCC",
    "[NaH]", "O=S(=O)(O)O", "CC(=O)O", "CNC", "NO", "CI", "O=P(O)(O)O",
    "O=C(O)/C=C/C(=O)O", "[OH-]", "O=C(O)[C@H](O)[C@@H](O)C(=O)O",
    "[O-][Cl+3]([O-])([O-])[O-]", "CS(=O)(=O)O", "[Zn]", "O=[N+]([O-])O",
    "O=C(O)/C=C\\C(=O)O", "NCCO", "O=C(O)C(O)C(O)C(=O)O",
    "O=S(=O)([O-])C(F)(F)F", "[O-][Cl+3]([O-])([O-])O", "NC(CO)(CO)CO",
    "CS(=O)(=O)[O-]",
}

def remove_salt(smiles):
    parts = [p for p in smiles.split(".") if p not in _SALT_FRAGMENTS]
    if not parts:
        return smiles
    if len(parts) == 1:
        return parts[0]
    return parts[int(np.argmax([len(p) for p in parts]))]

def generate_fingerprints(data, fpSize=2048, useFeatures=False):
    gen = rdFingerprintGenerator.GetMorganGenerator(
        radius=2, fpSize=fpSize,
        atomInvariantsGenerator=rdFingerprintGenerator.GetMorganFeatureAtomInvGen()
        if useFeatures else None)
    data = data.copy()
    n_total = len(data)
    data["canonical_smiles"] = data["canonical_smiles"].map(remove_salt)

    # Compute fps once per unique SMILES, then broadcast back to all rows
    unique_smi = data["canonical_smiles"].dropna().unique()
    print(f"  FP generation: {n_total} rows, {len(unique_smi)} unique SMILES")
    fp_map = {}
    n_invalid, n_too_small, n_too_large = 0, 0, 0
    for smi in unique_smi:
        mol = Chem.MolFromSmiles(smi) if isinstance(smi, str) else None
        if mol is None:
            n_invalid += 1
            continue
        n = mol.GetNumHeavyAtoms()
        if n < 3:
            n_too_small += 1
            continue
        if n > 999:
            n_too_large += 1
            continue
        fp_map[smi] = gen.GetFingerprint(mol).ToList()
    print(f"  Invalid SMILES: {n_invalid}, too small (<3 atoms): {n_too_small}, too large (>999): {n_too_large}")
    print(f"  Valid FPs: {len(fp_map)}")

    data = data[data["canonical_smiles"].isin(fp_map)].reset_index(drop=True)
    print(f"  Rows after FP filter: {len(data)}")
    fp_df = pd.DataFrame(
        [fp_map[smi] for smi in data["canonical_smiles"]],
        columns=range(fpSize))
    return pd.concat([data, fp_df], axis=1)

################################################################
## Fetch UniProt ID from UniProt accession
################################################################
def fetch_batch(batch):
	url = "https://rest.uniprot.org/uniprotkb/search"
	query_string = " OR ".join(f"accession:{acc}" for acc in batch)
	params = {
		"query": query_string,
		"fields": "accession,id",
		"format": "json",
		"size": len(batch),
	}
	response = requests.get(url, params=params)
	response.raise_for_status()
	return response.json().get("results", [])

def fetch_uniprot_id(accession_list):
	BATCH_SIZE = 30
	MAX_WORKERS = 4
	results = {}

	batches = [accession_list[i:i + BATCH_SIZE] for i in range(0, len(accession_list), BATCH_SIZE)]
	with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
		futures = {executor.submit(fetch_batch, batch): batch for batch in batches}
		for future in tqdm.tqdm(as_completed(futures), total=len(batches), desc="Fetching UniProt IDs"):
			try:
				for result in future.result():
					results[result.get("primaryAccession")] = result.get("uniProtkbId")
			except requests.exceptions.RequestException as e:
				print(f"API Request failed: {e}")
	return results

################################################################
## GEV parameterisation
################################################################

def fit_gev_params(freq_file, out_dir, tc_cutoff=0.29, bin_width=0.25):
    cache_file = os.path.join(out_dir, f"gev_params_tc{tc_cutoff}_bw{bin_width}.tsv")
    if not os.path.exists(freq_file):
        print(f"Warning: freq file not found, skipping GEV fit: {freq_file}")
        return
    print(f"Fitting GEV from {freq_file} ...")
    data    = pd.read_csv(freq_file, sep="\t", index_col=0)
    z_score = data["z_score"].to_numpy()
    count   = data["count"].to_numpy()
    in_range = (z_score >= -5) & (z_score <= 10)
    sample  = np.arange(-5, 10, bin_width).round(4)
    bin_idx = np.clip(np.digitize(z_score[in_range], sample) - 1, 0, len(sample) - 1)
    z_group = np.bincount(bin_idx, weights=count[in_range], minlength=len(sample))
    grouped_z = sample[z_group != 0]
    grouped_c = z_group[z_group != 0].astype(int)
    samples = np.repeat(grouped_z, grouped_c)
    c, loc, scale = genextreme.fit(samples)
    pd.DataFrame({
        "c":          [c],
        "loc":        [loc],
        "scale":      [scale],
        "bin_width":  [bin_width],
        "freq_file":  [freq_file],
    }).to_csv(cache_file, sep="\t")
    print(f"GEV params (c={c:.6f}, loc={loc:.6f}, scale={scale:.6f}) written to {cache_file}")

################################################################
## Read in sqlite database and convert to tsv file
################################################################

def sqlite_to_csv(sqlite_lib, aggregator_file=None):
	known_units = ", ".join(f"'{u}'" for u in ACTIVITY_UNIT_TRANSFORMS)
	query = f"""
	SELECT
		td.chembl_id AS target_chembl_id,
		td.pref_name AS target_name,
		cseq.sequence AS target_sequence,
		cseq.accession AS target_accession,
		td.target_type AS type,
		td.organism,
		md.chembl_id AS molecule_chembl_id,
		cs.canonical_smiles,
		act.standard_type,
		act.standard_value,
		act.standard_units,
		act.activity_comment,
		a.confidence_score
	FROM
		activities act
	JOIN
		assays a ON act.assay_id = a.assay_id
	JOIN
		target_dictionary td ON a.tid = td.tid
	JOIN
		molecule_dictionary md ON act.molregno = md.molregno
	JOIN
		compound_structures as cs ON md.molregno = cs.molregno
	JOIN
		target_components tc ON td.tid = tc.tid
	JOIN
		component_sequences cseq ON tc.component_id = cseq.component_id
	WHERE
		act.standard_value IS NOT NULL AND
		act.standard_units IN ({known_units}) AND
		cs.canonical_smiles IS NOT NULL AND
		td.target_type = 'SINGLE PROTEIN' AND
		a.confidence_score >= 7
	"""
	if not os.path.exists("tmp.tsv") :

		print("Reading sqlite database: {}".format(sqlite_lib))
		connect = sqlite3.connect("{}".format(sqlite_lib))
		connect.execute("PRAGMA cache_size = -131072")  # 128 MB page cache
		connect.execute("PRAGMA temp_store = MEMORY")   # keep temp tables in RAM
		connect.execute("PRAGMA mmap_size = 536870912") # 512 MB memory-mapped I/O
		connect.execute("PRAGMA journal_mode = WAL")
		data = pd.read_sql_query(query, connect)
		data = data.drop(columns = ["type"])
		connect.close()
		os.system("mkdir -p dataset")

		# write data to tmp file
		tmp_file = "tmp.tsv"
		data.to_csv(tmp_file, sep = "\t", index = False)
	
	else:
		print("Reading tmp file: tmp.tsv")
		data = pd.read_csv("tmp.tsv", sep="\t", dtype={"activity_comment": str})


	# Apply type transform then unit multiplier to get activity in nM
	n0 = len(data)
	type_fns  = data["standard_type"].map(ACTIVITY_TYPE_TRANSFORMS)
	unit_mult = data["standard_units"].map(ACTIVITY_UNIT_TRANSFORMS)
	data["activity_nM"] = [
		fn(v) * u if (callable(fn) and not pd.isna(u) and not pd.isna(v)) else float("nan")
		for fn, v, u in zip(type_fns, data["standard_value"], unit_mult)
	]
	mask_type    = data["standard_type"].isin(ACTIVITY_TYPE_TRANSFORMS)
	mask_unit    = data["standard_units"].isin(ACTIVITY_UNIT_TRANSFORMS)
	mask_value   = data["activity_nM"].notna() & (data["activity_nM"] > 0) & (data["activity_nM"] <= 10000)
	print(f"Activity filtering (starting from {n0} rows):")
	print(f"  unknown standard_type:    {(~mask_type).sum()} rows removed")
	print(f"  unknown standard_units:   {(~mask_unit).sum()} rows removed")
	print(f"  activity out of range:    {(~mask_value).sum()} rows removed")
	data = data[mask_type & mask_unit & mask_value].reset_index(drop=True)
	print(f"  remaining: {len(data)} rows")

	# Remove compounds similar to known aggregators (Tc >= 0.7 by ECFP4)
	if aggregator_file and os.path.exists(aggregator_file):
		data = filter_aggregators(data, aggregator_file)
	elif aggregator_file:
		print(f"Warning: aggregator file not found: {aggregator_file}")

	# Convert uniprot accession to uniprot ID and add as a column
	uniprot_accessions = [acc for acc in data["target_accession"].dropna().unique() if len(acc) <= 8]
	print("Fetching UniProt IDs for {} accessions...".format(len(uniprot_accessions)))
	uniprot_results = fetch_uniprot_id(uniprot_accessions)
	data["uniprot_id"] = data["target_accession"].map(uniprot_results)

	# Write uniprot_chembl_id_uniprot_info.tsv
	info_df = (
		data[["target_chembl_id", "target_name", "organism", "target_accession", "target_sequence", "uniprot_id"]]
		.drop_duplicates(subset=["target_chembl_id", "target_accession"])
		.rename(columns={"target_accession": "uniprot_accession", "target_sequence": "sequence"})
		.reset_index(drop=True)
	)
	info_path = os.path.join("dataset", "uniprot_chembl_id_uniprot_info.tsv")
	os.makedirs("dataset", exist_ok=True)
	info_df.to_csv(info_path, sep="\t")
	print(f"Wrote {len(info_df)} rows to {info_path}")

	# Generate fingerprints once across all compounds before splitting by target
	print("Generating fingerprints...")
	data = generate_fingerprints(data)
	if data.shape[0] == 0:
		print("All compounds filtered during fingerprint generation")
		return data

	def _write_target(args):
		target, group = args
		result = group.sort_values(by=["standard_value"], ignore_index=True)
		result = result.drop_duplicates(subset=["molecule_chembl_id"], ignore_index=True)
		result = result.drop(columns=["activity_comment"], errors="ignore")
		if result.shape[0] == 0:
			return target, 0
		organism = result["organism"].iloc[0]
		if len(organism.split()) > 1:
			organism = organism.split()[0]
		out_dir = os.path.join("dataset", organism)
		os.makedirs(out_dir, exist_ok=True)
		result.to_csv(os.path.join(out_dir, f"{target}.tsv"), sep="\t")
		return target, result.shape[0]

	print("Writing tsv files to dataset/ directory")
	groups = list(data.groupby("target_chembl_id"))
	with ThreadPoolExecutor(max_workers=min(16, os.cpu_count())) as pool:
		futures = {pool.submit(_write_target, g): g[0] for g in groups}
		filtered = []
		for future in tqdm.tqdm(as_completed(futures), total=len(futures), desc="Writing targets"):
			target, n = future.result()
			if n == 0:
				filtered.append(target)
	if filtered:
		print(f"{len(filtered)} targets filtered out after fingerprint generation: {filtered}")

	return data.sort_values(by = "target_chembl_id", ignore_index = True)

################################################################
## Process sqlite library
################################################################

if __name__ == "__main__":
    sqlite_lib = sys.argv[1]

    if not os.path.exists(AGGREGATORS_FILE):
        print("Missing aggregator file in this directory!")
        sys.exit(1)
    data = sqlite_to_csv(sqlite_lib, aggregator_file=AGGREGATORS_FILE)
    freq_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.29_orig_freq.tsv")
    fit_gev_params(freq_file, out_dir="dataset")
    print("Dataset DONE")


