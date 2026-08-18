import os

_TARGET_FILES_CACHE = None


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
