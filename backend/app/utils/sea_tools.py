import os

_TARGET_FILES_CACHE = None

TC_CUTOFF   = 0.29
GEV_BIN_WIDTH = 0.25


def gev_paths() -> tuple[str, str]:
    """Return (freq_file, cache_file) paths for GEV parameterisation.

    freq_file  — raw null-set histogram, lives inside SEA_DATA_FOLDER (may be read-only).
    cache_file — fitted params cache, written to GEV_CACHE_DIR (must be writable).
                 GEV_CACHE_DIR defaults to SEA_DATA_FOLDER for backwards compat with
                 datasets that already ship the cache file.
    """
    data_folder = os.getenv("SEA_DATA_FOLDER", "")
    cache_dir   = os.getenv("GEV_CACHE_DIR", data_folder)
    freq_file   = os.path.join(data_folder, f"{TC_CUTOFF}_orig_freq.tsv")
    cache_file  = os.path.join(cache_dir,   f"gev_params_tc{TC_CUTOFF}_bw{GEV_BIN_WIDTH}.tsv")
    return freq_file, cache_file


def get_target_files() -> list[str]:
    """Cache and return list of target .tsv files from SEA_DATA_FOLDER."""
    global _TARGET_FILES_CACHE
    if _TARGET_FILES_CACHE is None:
        data_folder = os.getenv("SEA_DATA_FOLDER")
        _TARGET_FILES_CACHE = []
        for folder in os.listdir(data_folder):
            if not os.path.isdir(os.path.join(data_folder, folder)):
                continue
            for f in os.listdir(os.path.join(data_folder, folder)):
                full_path = os.path.join(data_folder, folder, f)
                if os.path.isfile(full_path) and full_path.endswith(".tsv"):
                    _TARGET_FILES_CACHE.append(full_path)
    return _TARGET_FILES_CACHE
