## Import module
import os
import sys
import shlex
import string
from tqdm import tqdm
import numpy as np
import pandas as pd


################################################################
## Generate Boltz-2 cofolding jobs from SEA results
## This is the script used for turning SEA high confidence hits
## into Boltz-2 cofolding inputs (protein + ligand, MSA enabled)
## Consumes the result_<base>/ folder written by run_sea_*.py :
##	sea_result.tsv                       -- high confidence targets
##	                                        (CHEMBL ID, Target name,
##	                                         Uniprot ID, Sequence, Best Tc)
##	best_similar_<base>_<CHEMBLID>.tsv   -- query ligands per target
##	                                        (Name, SMILES, Tanimoto Coefficient)
##
## For every high confidence target x query ligand pair it writes a
## Boltz-2 input YAML plus a shell script that runs
## `boltz predict ... --use_msa_server` so the MSA is built
## automatically by the ColabFold MMseqs2 server.
##
## Multi-chain proteins
## 	The SEA / ChEMBL lookup stores one sequence per target, so the oligomeric
## 	state cannot be inferred.  Monomer-only policy: every entity is folded as
## 	a SINGLE copy -- a chain is never repeated.
## 		"copies" in stoich_overrides is an annotation only; a value > 1
## 			prints a warning and the MONOMER is still folded
## 		several distinct sequences under one CHEMBL ID are folded together
## 			as one complex (auto_complex, default on) with a warning,
## 			since they may be grouped orthologs and not real subunits
## 		"partners" adds distinct chains explicitly (a genuine hetero-
## 			complex), by Uniprot accession or by explicit sequence
## 	Identical sequences are de-duplicated.  Chains take ids A, B, C, ... in
## 	order and the ligand takes the next free id.
## 	See the Input variables section for how to set these per target.
################################################################


################################################################
## Helper function
################################################################

def _mkdir_(files) :
	cmd = '''mkdir -p {}'''.format(files)
	os.system(cmd)
	return

def sanitize(name) :
	'''make a string safe for use as a file / directory name'''
	keep = "-_."
	return "".join(c if c.isalnum() or c in keep else "_" for c in str(name)).strip("_")

def id_stream() :
	'''yield Boltz chain ids: A..Z then AA..ZZ'''
	for c in string.ascii_uppercase :
		yield c
	for c in string.ascii_uppercase :
		for d in string.ascii_uppercase :
			yield c + d

################################################################
## Read SEA output
################################################################

def read_targets(result_dir) :
	'''
	result_dir	str	path to a result_<base>/ folder

	Read sea_result.tsv and return the high confidence targets.  Rows without a
	usable protein sequence are dropped.  A single CHEMBL ID may span several
	rows (ChEMBL complex / family targets that map to more than one Uniprot
	accession); those rows are kept and grouped later.
	'''
	sea_file = os.path.join(result_dir, "sea_result.tsv")
	if not os.path.exists(sea_file) :
		raise RuntimeError("Error: {} not found".format(sea_file))

	targets = pd.read_csv(sea_file, sep = "\t")

	for col in ["Target_Chembl_ID", "Sequence"] :

		if col not in targets.columns :
			raise RuntimeError("Error: column '{}' missing from {}. Re-run SEA with the "
				"Uniprot / sequence annotation enabled.".format(col, sea_file))

	## Drop targets without a real sequence -- nothing to cofold against
	targets = targets[targets["Sequence"].notna()]
	targets = targets[targets["Sequence"].astype(str).str.strip() != ""]
	targets = targets.reset_index(drop = True)

	if targets.shape[0] == 0 :
		raise RuntimeError("Error: no high confidence targets with a sequence in {}".format(sea_file))

	return targets

def read_ligands(result_dir, base, chembl_id, testFile, tc_min, top_n) :
	'''
	result_dir	str	path to a result_<base>/ folder
	base		str	test set name (result folder without the result_ prefix)
	chembl_id	str	target CHEMBL accession
	testFile	str	raw test SMILES file (ligand fallback)
	tc_min		float	keep ligands with Tanimoto Coefficient >= this
	top_n		int	keep at most this many ligands per target (None = all)

	Return the query ligands to cofold against one target.  Prefers the
	per-target best_similar_<base>_<chembl_id>.tsv (carries the Tanimoto
	Coefficient); falls back to the raw test SMILES file when that is absent.
	Returns a DataFrame with columns Name, SMILES, Tc.
	'''
	best_file = os.path.join(result_dir, "best_similar_{}_{}.tsv".format(base, chembl_id))
	if not os.path.exists(best_file) :
		best_file = os.path.join(result_dir, "best_similar_{}.tsv".format(chembl_id))

	use_fallback = True
	if os.path.exists(best_file) :
		ligands = pd.read_csv(best_file, sep = "\t")
		ligands = ligands.rename(columns = {"Tanimoto Coefficient" : "Tc", "Tanimoto_Coefficient" : "Tc"})
		ligands = ligands[["Name", "SMILES", "Tc"]].copy()
		ligands = ligands[ligands["Tc"] >= tc_min]
		ligands = ligands.sort_values(by = ["Tc"], ascending = False, ignore_index = True)
		if ligands.shape[0] > 0 :
			use_fallback = False

	if use_fallback :
		## Fallback: raw test file, "<SMILES> <Name>" per line, no Tc available
		## (triggered when best_similar doesn't exist or was filtered empty by the
		## upstream Tc cutoff in the celery worker)
		print("  Note: best_similar empty/absent for {}; falling back to {}".format(chembl_id, testFile))
		rows = np.loadtxt(testFile, comments = None, dtype = str)
		if rows.ndim == 1 : rows = rows.reshape(1, -1)
		ligands = pd.DataFrame()
		ligands["Name"]   = rows[:, 1]
		ligands["SMILES"] = rows[:, 0]
		ligands["Tc"]     = np.nan

	if top_n is not None and top_n > 0 :
		ligands = ligands.head(top_n)

	return ligands.reset_index(drop = True)

################################################################
## Assemble protein chains for a target
################################################################

def build_chains(chembl_id, seq_rows, override, acc2seq, auto_complex) :
	'''
	chembl_id	str	target CHEMBL accession
	seq_rows	list	dicts {sequence, accession} -- every lookup row that
				shares this CHEMBL ID (usually one)
	override	dict	stoich_overrides entry for this CHEMBL ID (or None)
	acc2seq		dict	Uniprot accession -> sequence, for partner resolution
	auto_complex	bool	fold several distinct lookup sequences together as one
				complex (True) or keep only the first (False)

	Resolve the list of protein chains to fold for one target.  Monomer-only
	policy: every entity is folded as a single copy; a "copies" annotation > 1
	is honoured only as a warning, never as repeated chains.
	Returns a list of chains, each {sequence, label}.
	'''
	override = override if override else {}
	chains   = []
	seen     = set()

	def add(sequence, label, requested = 1) :
		if int(requested) > 1 :
			print("  Warning: {} chain '{}' is annotated as {} copies; folding "
				"monomer only (monomer-only policy).".format(chembl_id, label, requested))
		seq = str(sequence).strip()
		if seq in seen : return
		seen.add(seq)
		chains.append({"sequence" : seq, "label" : label})

	## Primary chain: the target's own sequence (always a single copy)
	primary = seq_rows[0]
	add(primary["sequence"], primary["accession"] if primary["accession"] else chembl_id, override.get("copies", 1))

	## Extra lookup rows for the same CHEMBL ID: fold all distinct sequences
	## together as one complex (with a warning), unless auto_complex is off
	if len(seq_rows) > 1 :
		accs = [str(seq_rows[idx]["accession"]) for idx in np.arange(len(seq_rows))]
		if auto_complex :
			for idx in np.arange(1, len(seq_rows)) :
				add(seq_rows[idx]["sequence"], seq_rows[idx]["accession"] if seq_rows[idx]["accession"] else chembl_id)
			print("  Warning: {} maps to {} sequences ({}); folding them together "
				"as a complex -- verify this is a genuine hetero-complex and not "
				"grouped orthologs.".format(chembl_id, len(seq_rows), ", ".join(accs)))
		else :
			print("  Warning: {} maps to {} sequences ({}); using the first only "
				"(auto_complex = False).".format(chembl_id, len(seq_rows), ", ".join(accs)))

	## Explicit partner chains from the override (distinct entities, one copy each)
	partners = override.get("partners", [])
	for idx in np.arange(len(partners)) :
		p = partners[idx]
		if isinstance(p, str) :
			seq = acc2seq.get(p)
			if seq is None :
				raise RuntimeError("Error: partner accession '{}' for {} not found in the "
					"lookup file".format(p, chembl_id))
			add(seq, p)
		else :
			if "sequence" not in p :
				raise RuntimeError("Error: partner dict for {} needs a 'sequence' key".format(chembl_id))
			add(p["sequence"], p.get("label", "partner"), p.get("copies", 1))

	return chains

################################################################
## Write Boltz-2 input YAML
################################################################

def write_yaml(yaml_path, protein_chains, smiles, msa_mode, msa_dir, predict_affinity) :
	'''
	yaml_path	str	output YAML path
	protein_chains	list	dicts {sequence, label}; monomer-only policy, so each
				is written as ONE chain with its own id and its own MSA
	smiles		str	ligand SMILES
	msa_mode	str	"server"      -- omit msa; built by --use_msa_server
				"precomputed" -- msa: <msa_dir>/<label>.a3m per chain
				"single"      -- msa: empty (single sequence, no MSA)
	msa_dir		str	folder of precomputed .a3m files
	predict_affinity boolean	add a binding affinity property for the ligand

	Write a single Boltz-2 cofolding input YAML for one or more protein chains
	plus one ligand.  Returns (ligand_id, protein chain count).
	'''
	ids   = id_stream()
	lines = ["version: 1", "sequences:"]

	for idx in np.arange(len(protein_chains)) :
		chain = protein_chains[idx]
		lines.append("  - protein:")
		lines.append("      id: {}".format(next(ids)))
		lines.append("      sequence: {}".format(chain["sequence"]))
		if msa_mode == "precomputed" :
			lines.append("      msa: {}".format(os.path.join(msa_dir, "{}.a3m".format(sanitize(chain["label"])))))
		elif msa_mode == "single" :
			lines.append("      msa: empty")
		## msa_mode == "server" -> leave msa unset so --use_msa_server fills it in

	ligand_id = next(ids)
	lines.append("  - ligand:")
	lines.append("      id: {}".format(ligand_id))
	lines.append("      smiles: '{}'".format(smiles))

	if predict_affinity :
		lines.append("properties:")
		lines.append("  - affinity:")
		lines.append("      binder: {}".format(ligand_id))

	with open(yaml_path, "w") as fh :
		fh.write("\n".join(lines) + "\n")
	return ligand_id, len(protein_chains)

################################################################
## Build all jobs
################################################################

def generate_jobs(result_dir, testFile, out_dir, tc_min = 0.0, top_n = None, msa_mode = "server",
		msa_dir = "msa", predict_affinity = True, boltz_out_dir = "boltz_out", boltz_extra_args = "",
		lookup_file = "uniprot_chembl_id.tsv", stoich_overrides = {}, auto_complex = True,
		log_file = None, boltz_bin = "/nfs/home/khtang/.conda/envs/sea25/bin/boltz") :
	'''
	result_dir	str	SEA result folder (result_<base>/)
	testFile	str	raw test SMILES file (ligand fallback)
	out_dir		str	folder to write the generated jobs into
	tc_min		float	keep ligand/target pairs with Tanimoto Coefficient >= this
	top_n		int	keep at most this many ligands per target (None = all)
	msa_mode	str	"server" / "precomputed" / "single" (see write_yaml)
	msa_dir		str	folder of precomputed .a3m files
	predict_affinity boolean	add a binding affinity property for the ligand
	boltz_out_dir	str	Boltz-2 output sub-folder inside each job
	boltz_extra_args str	extra CLI arguments passed to `boltz predict`
	lookup_file	str	uniprot_chembl_id.tsv (resolves partner accessions)
	stoich_overrides dict	per CHEMBL ID assembly overrides
	auto_complex	boolean	fold several lookup sequences together as a complex

	For each high confidence target x ligand pair write an input YAML plus a
	per-job run script.  Also emit a master run script and a job manifest.
	'''
	from datetime import datetime
	def _log(msg) :
		if log_file :
			ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
			with open(log_file, "a") as _f :
				_f.write(f"[{ts}] {msg}\n")

	base    = os.path.basename(os.path.normpath(result_dir))
	base    = base[len("result_") :] if base.startswith("result_") else base
	targets = read_targets(result_dir)
	_mkdir_(out_dir)

	## Accession -> sequence map (for resolving partner chains by accession)
	acc2seq = {}
	if os.path.exists(lookup_file) :
		lookup  = pd.read_csv(lookup_file, sep = "\t", index_col = 0)
		acc2seq = dict(zip(lookup["uniprot_accession"].astype(str), lookup["sequence"].astype(str)))
	elif any("partners" in (ov if ov else {}) and any(isinstance(p, str) for p in ov["partners"])
			for ov in stoich_overrides.values()) :
		raise RuntimeError("Error: lookup file {} needed to resolve partner accessions".format(lookup_file))

	## Walk the high confidence targets and emit one job per target x ligand
	chembl_ids  = pd.unique(targets["Target_Chembl_ID"].astype(str))
	has_uniprot = "Uniprot_ID"  in targets.columns
	has_name    = "Target_Name" in targets.columns

	job_scripts = []
	used_names  = set()
	m_job = []; m_chembl = []; m_uniprot = []; m_tname = []; m_nchain = []
	m_labels = []; m_ligand = []; m_tc = []; m_smiles = []; m_yaml = []; m_run = []
	n_total = len(chembl_ids)
	log_fh  = open(log_file, "a") if log_file else None
	bar = tqdm(chembl_ids, desc = "Boltz jobs", unit = "target", file = log_fh,
		dynamic_ncols = False, mininterval = 0, miniters = 1,
		bar_format = "{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]")
	for idxT, chembl_id in enumerate(bar) :
		rows      = targets[targets["Target_Chembl_ID"].astype(str) == chembl_id]
		tname     = rows["Target_Name"].tolist()[0] if has_name    else ""
		uniprot   = rows["Uniprot_ID"].tolist()[0]  if has_uniprot else ""

		seqs     = rows["Sequence"].tolist()
		accs     = rows["Uniprot_ID"].tolist() if has_uniprot else [None] * len(seqs)
		seq_rows = [{"sequence" : seqs[idx], "accession" : accs[idx]} for idx in np.arange(len(seqs))]

		chains = build_chains(chembl_id, seq_rows, stoich_overrides.get(chembl_id), acc2seq, auto_complex)
		labels = "+".join(str(chains[idx]["label"]) for idx in np.arange(len(chains)))

		ligands = read_ligands(result_dir, base, chembl_id, testFile, tc_min, top_n)
		if ligands.shape[0] == 0 :
			bar.write("  {}: no ligand above Tc >= {}; skipped".format(chembl_id, tc_min))
			continue

		for idxL in np.arange(ligands.shape[0]) :
			lig      = ligands.iloc[idxL]
			lig_name = sanitize(lig["Name"])
			smiles   = str(lig["SMILES"]).strip()
			job_name = "{}__{}".format(sanitize(chembl_id), lig_name)

			## Two ligand names can sanitize to the same string (e.g. "cpd/1"
			## and "cpd:1"); suffix the job so it never silently overwrites
			if job_name in used_names :
				suffix = 2
				while "{}_{}".format(job_name, suffix) in used_names : suffix += 1
				bar.write("  Note: job name {} already used; writing '{}' as {}_{}".format(
					job_name, lig["Name"], job_name, suffix))
				job_name = "{}_{}".format(job_name, suffix)
			used_names.add(job_name)

			job_dir  = os.path.join(out_dir, job_name)
			_mkdir_(job_dir)

			yaml_path  = os.path.join(job_dir, "{}.yaml".format(job_name))
			_, n_chain = write_yaml(yaml_path, chains, smiles, msa_mode, msa_dir, predict_affinity)

			if predict_affinity and n_chain > 1 :
				bar.write("  Note: {} has {} protein chains; affinity is trained on "
					"single-chain complexes -- interpret with care.".format(job_name, n_chain))

			## Cache flags on local
			cache_flag = '--cache /scratch/sea_boltz '
			## Per-job run script -- paths are quoted so folders with spaces work
			server_flag = "--use_msa_server " if msa_mode == "server" else ""
			cmd = "{bin} predict {yaml} --out_dir {out} {server}{cache}{extra}".format(
				bin    = boltz_bin,
				yaml   = shlex.quote(os.path.abspath(yaml_path)),
				out    = shlex.quote(os.path.abspath(os.path.join(job_dir, boltz_out_dir))),
				server = server_flag,
				cache  = cache_flag,
				extra  = boltz_extra_args).strip()
			log_path = os.path.join(job_dir, "{}.log".format(job_name))
			print(log_path)
			run_path = os.path.join(job_dir, "run.sh")
			with open(run_path, "w") as fh :
				fh.write(
					"#!/usr/bin/env bash\n"
					"set -euo pipefail\n"
					"export LD_LIBRARY_PATH=/nfs/home/khtang/.conda/envs/sea25/lib\n"
					"export CUDA_VISIBLE_DEVICES=2\n"
					"{} 2>&1 | tee {}\n".format(cmd, shlex.quote(os.path.abspath(log_path)))
				)
			os.chmod(run_path, 0o755)
			job_scripts.append(os.path.abspath(run_path))

			m_job.append(job_name)
			m_chembl.append(chembl_id)
			m_uniprot.append(uniprot)
			m_tname.append(tname)
			m_nchain.append(n_chain)
			m_labels.append(labels)
			m_ligand.append(lig["Name"])
			m_tc.append(lig["Tc"])
			m_smiles.append(smiles)
			m_yaml.append(os.path.abspath(yaml_path))
			m_run.append(os.path.abspath(run_path))

		_log("[{}/{}] {} — {} ligand(s)".format(idxT + 1, n_total, chembl_id, ligands.shape[0]))

	bar.close()
	if log_fh :
		log_fh.close()

	if len(m_job) == 0 :
		raise RuntimeError("No jobs generated (no target/ligand pairs passed the filters).")

	## Master run script -- runs every job sequentially
	master_path = os.path.join(out_dir, "run_all.sh")
	with open(master_path, "w") as fh :
		fh.write("#!/usr/bin/env bash\nset -euo pipefail\n")
		fh.write("## Generated by generate_boltz_jobs.py -- {} jobs\n".format(len(job_scripts)))
		for idx in np.arange(len(job_scripts)) :
			fh.write("bash {}\n".format(shlex.quote(job_scripts[idx])))
	os.chmod(master_path, 0o755)

	## Job manifest
	manifest = pd.DataFrame()
	manifest["job"]            = m_job
	manifest["CHEMBL ID"]      = m_chembl
	manifest["Uniprot ID"]     = m_uniprot
	manifest["Target name"]    = m_tname
	manifest["Protein chains"] = m_nchain
	manifest["Chain labels"]   = m_labels
	manifest["Ligand"]         = m_ligand
	manifest["Tc"]             = m_tc
	manifest["SMILES"]         = m_smiles
	manifest["yaml"]           = m_yaml
	manifest["run"]            = m_run
	manifest_path = os.path.join(out_dir, "boltz_manifest.tsv")
	manifest.to_csv(manifest_path, sep = "\t", index = False)

	print("Generated {} Boltz-2 cofolding jobs across {} targets".format(
		manifest.shape[0], manifest["CHEMBL ID"].nunique()))
	print("  jobs     : {}/".format(out_dir))
	print("  manifest : {}".format(manifest_path))
	print("  run all  : bash {}".format(master_path))
	return manifest


################################################################
## Input variables
################################################################

if __name__ == "__main__" :

	## SEA result folder (result_<base>/ written by run_sea_*.py) and the raw
	## test SMILES file (only used as a ligand fallback when a target has no
	## best_similar_*.tsv)
	result_dir = sys.argv[1] if len(sys.argv) > 1 else "result_test"
	testFile   = sys.argv[2] if len(sys.argv) > 2 else "test.smi"

	## Where to write the generated jobs
	out_dir    = "boltz_jobs"

	## Ligand selection per target
	tc_min     = 0.0     # keep ligand/target pairs with Tanimoto Coefficient >= this
	top_n      = None    # keep at most this many ligands per target (None = all)

	## MSA handling
	## 	"server"      -- MSA built automatically by the ColabFold MMseqs2
	## 	                 server at predict time (boltz --use_msa_server)
	## 	"precomputed" -- use a per-chain .a3m in msa_dir named <label>.a3m,
	## 	                 where <label> is the Uniprot accession
	## 	"single"      -- single sequence, no MSA
	msa_mode   = "server"
	msa_dir    = "msa"

	## Protein chain composition (monomer-only policy)
	## 	The target's own chain is always folded as a single copy.  Overrides:
	## 	stoich_overrides -- per CHEMBL ID dict that may set:
	## 	                      "copies"  : annotation only; if > 1 a warning is
	## 	                                  printed and the MONOMER is folded
	## 	                      "partners": distinct extra chains (a genuine hetero-
	## 	                                  complex), each a Uniprot accession
	## 	                                  (resolved via lookup) or {"sequence": ..}
	## 	auto_complex     -- if a CHEMBL ID maps to several distinct lookup
	## 	                    sequences, fold them all together as one complex (with
	## 	                    a warning).  Set False to use only the first sequence.
	stoich_overrides = {
		# "CHEMBL1075021" : {"copies": 2},              # homo-dimer -> warns, folds monomer
		# "CHEMBL3390824" : {"partners": ["Q9P8R0"]},   # hetero-complex partner chain
	}
	auto_complex     = True

	## Lookup TSV used to resolve partner chains given by Uniprot accession
	lookup_file      = "uniprot_chembl_id.tsv"

	## Predict binding affinity for the ligand in addition to the structure
	predict_affinity = True

	## Boltz-2 output sub-folder (inside each job) and any extra CLI arguments
	## (e.g. "--recycling_steps 3 --diffusion_samples 5 --use_potentials")
	boltz_out_dir    = "boltz_out"
	boltz_extra_args = ""


	################################################################
	## Run analysis
	################################################################

	sea_boltz = generate_jobs(result_dir, testFile, out_dir,
		tc_min           = tc_min,
		top_n            = top_n,
		msa_mode         = msa_mode,
		msa_dir          = msa_dir,
		predict_affinity = predict_affinity,
		boltz_out_dir    = boltz_out_dir,
		boltz_extra_args = boltz_extra_args,
		lookup_file      = lookup_file,
		stoich_overrides = stoich_overrides,
		auto_complex     = auto_complex)
