# SEA Pipeline

## Overview

  Generate ChEMBL library for SEA 2026
   - Extract and filter activity data from a ChEMBL SQLite dump
   - fetch UniProt metadata
   - generate fingerprints
   - write per-target TSVs and a target-info lookup file
---

## Setup

**Download ChEMBL sqlite file**

```
wget https://ftp.ebi.ac.uk/pub/databases/chembl/ChEMBLdb/latest/chembl_<version>_sqlite.tar.gz
tar xvf chembl_<version>_sqlite.tar.gz
```

**Setup Python environment**

- Install these Dependencies in your prefered Python environment

```
    rdkit
    pandas
    numpy
    scipy
    tqdm // alive_progress
    requests
```

## How to use
**Script:** `sqlite_to_csv.py`

```bash
python sqlite_to_csv.py <path/to/chembl.db>
```

Internally runs the following steps in order:

1. **SQL query** — joins `activities`, `assays`, `target_dictionary`, `molecule_dictionary`, `compound_structures`, `component_sequences`. Keeps single-protein targets with confidence score ≥ 7, non-null SMILES, and known activity units.
2. **Caches** raw output to `tmp.tsv` — re-runs skip the slow SQL query and read from cache instead.
3. **Activity normalization** — applies per-type transforms (identity / −log / +log) and per-unit multipliers to convert all values to nM. Rows outside 0 < activity ≤ 10,000 nM are dropped.
4. **Aggregator filtering** — ECFP4 Tanimoto similarity (Tc) against `aggregators_050219.tsv`. Removes rows where Tc ≥ 0.7 **and** ALogP > 3.5 **and** activity > 1,000 nM. Polar or very potent compounds are kept even if they resemble a known aggregator. Removed compounds are written to `aggregators_removed.tsv`.
5. **UniProt ID lookup** — batched REST calls to `rest.uniprot.org` (batch size 30, 4 workers). Maps each target accession to a UniProt entry name.
6. **Target info file** — writes `dataset/uniprot_chembl_id_uniprot_info.tsv` with one row per unique `(target_chembl_id, uniprot_accession)`: target name, organism, accession, sequence, and UniProt entry name. Used by the SEA backend for Boltz job generation.
7. **Fingerprint generation** — salt-stripped ECFP4 (Morgan radius 2, 2048 bits). Drops molecules with < 3 or > 999 heavy atoms and invalid SMILES.
8. **Per-target TSV output** — writes `dataset/<Organism>/<CHEMBL_TARGET_ID>.tsv`, deduplicated by `molecule_chembl_id` and sorted by `standard_value`. Each file includes the 2048 fingerprint bit-columns (named `0`…`2047`) used by the SEA scoring worker.

**Key paths:**
- Aggregator file: `aggregators_050219.tsv`
- Output directory: `dataset/`

