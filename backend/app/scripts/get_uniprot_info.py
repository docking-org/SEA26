import sys
import tqdm
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# Define the UniProt REST API search endpoint
url = "https://rest.uniprot.org/uniprotkb/search"

ifile = sys.argv[1]  # Input file containing UniProt accessions

# read the tsv input file and extract the UniProt accessions
with open(ifile, 'r') as f:
    lines = f.readlines()
header = lines[0].strip()
rows = [line.strip() for line in lines[1:]]
accessions = [row.split('\t')[4] for row in rows]

print(f"Searching UniProt for {len(accessions)} accessions...")

BATCH_SIZE = 20
MAX_WORKERS = 8
results = {}

def fetch_batch(batch):
    query_string = " OR ".join(f"accession:{acc}" for acc in batch)
    params = {
        "query": query_string,
        "fields": "accession,id",
        "format": "json",
        "size": BATCH_SIZE,
    }
    response = requests.get(url, params=params)
    response.raise_for_status()
    return response.json().get("results", [])

batches = [accessions[i:i + BATCH_SIZE] for i in range(0, len(accessions), BATCH_SIZE)]

with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
    futures = {executor.submit(fetch_batch, batch): batch for batch in batches}
    for future in tqdm.tqdm(as_completed(futures), total=len(batches), desc="Fetching UniProt IDs", unit="batch"):
        try:
            for result in future.result():
                results[result.get("primaryAccession")] = result.get("uniProtkbId")
        except requests.exceptions.RequestException as e:
            print(f"API Request failed: {e}")

# write the original data with uniprot_id appended as a new column
ofile = ifile.replace(".tsv", "_uniprot_info.tsv")
with open(ofile, 'w') as fout:
    fout.write(header + "\tuniprot_id\n")
    for row, acc in zip(rows, accessions):
        uniprot_id = results.get(acc, "Not Found")
        fout.write(f"{row}\t{uniprot_id}\n")
