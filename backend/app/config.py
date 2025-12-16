import os
from dotenv import load_dotenv

dotenv_path = os.path.join(os.path.dirname(__file__), "../.env")
load_dotenv()

class Config(object):
    SEA_DATA_FOLDER = os.getenv("SEA_DATA_FOLDER")
    SEA_SCRIPT = os.getenv("SEA_SCRIPT")
    SEA_JOB_DIR = os.getenv("SEA_JOB_DIR")